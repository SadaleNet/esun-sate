import hashlib
import json
import random
import sqlite3
import os
import time
import uuid
from flask import Flask, url_for, render_template, send_from_directory, send_file, abort, request, redirect

app = Flask(__name__)
app.config.from_file("./config.json", load=json.load)
app.config.from_file("./.config_secret.json", load=json.load)

CAPTCHA = ['kala', 'kasi', 'kili', 'kiwen', 'len', 'lipu', 'luka', 'mani', 'mun', 'noka', 'pan', 'pipi', 'poki', 'soweli', 'tomo', 'waso']


def connect_database():
	con = sqlite3.connect(app.config["DATABASE"], autocommit=True)
	cur = con.cursor()
	cur.execute("""
CREATE TABLE IF NOT EXISTS orders (
	session_id TEXT UNIQUE NOT NULL,
	status INTEGER NOT NULL,
	warehouse TEXT NOT NULL,
	address_recipient TEXT NOT NULL,
	address_phone TEXT,
	address_email TEXT,
	address_line1 TEXT NOT NULL,
	address_line2 TEXT,
	address_line3 TEXT,
	address_line4 TEXT,
	address_city TEXT NOT NULL,
	address_zip TEXT,
	address_country TEXT NOT NULL,
	contact TEXT NOT NULL,
	ip TEXT,
	ref TEXT,
	message TEXT
	);
""")
	cur.execute("""
CREATE TABLE IF NOT EXISTS status_change (
	order_id INTEGER NOT NULL,
	datetime INTEGER NOT NULL,
	status INTEGER NOT NULL,
	FOREIGN KEY(order_id) REFERENCES orders(id)
	);
""")
	cur.execute("""
CREATE TABLE IF NOT EXISTS inventory_checkout (
	order_id INTEGER NOT NULL,
	item TEXT NOT NULL,
	quantity INTEGER NOT NULL,
	price_each REAL NOT NULL,
	FOREIGN KEY(order_id) REFERENCES orders(id)
	);
""")
	cur.execute("""
CREATE TABLE IF NOT EXISTS inventory_list (
	item TEXT UNIQUE NOT NULL,
	quantity_ante INTEGER NOT NULL,
	quantity_us INTEGER NOT NULL
	);
""")
	# TODO: Make old orders stale here!
	cur.close()
	return con

def get_available_stock():
	con = connect_database()
	total = {warehouse:{k:0 for k in app.config["LISTING"].keys()} for warehouse in ["US", "ANTE"]}
	available = {warehouse:{k:0 for k in app.config["LISTING"].keys()} for warehouse in ["US", "ANTE"]}

	# Compute the consumed stock here
	cur = con.cursor()
	cur.execute("SELECT item, quantity_ante, quantity_us FROM inventory_list")
	for i in cur.fetchall():
		total["ANTE"][i[0]] = i[1]
		total["US"][i[0]] = i[2]
		available["ANTE"][i[0]] = i[1]
		available["US"][i[0]] = i[2]

	# Compute the consumed stock here
	cur.execute("""
SELECT orders.warehouse, item, COUNT(*) FROM inventory_checkout, orders
	WHERE inventory_checkout.order_id = orders.rowid AND orders.status >= 0
	GROUP BY orders.warehouse, inventory_checkout.item;
""")
	for i in cur.fetchall():
		if i[0] in available:
			if i[1] == "pokitawa":
				continue # No availability counter for pokitawa
			available[i[0]][i[1]] -= i[2]
	cur.close()
	con.close()

	return {"total_ante": total["ANTE"], "total_us": total["US"],
			"available_ante": available["ANTE"], "available_us": available["US"]}

def compute_challenge_hash(session_id, image_id):
	m = hashlib.sha256()
	m.update(session_id.encode())
	m.update(image_id.encode())
	m.update(app.config["SALT"].encode())
	return m.hexdigest()

def check_auth():
	# TODO: check cookie
	return True

@app.route('/', methods=['GET', 'POST'])
def form():
	stock = get_available_stock()
	available = stock["available_ante"]
	available_us = stock["available_us"]

	error_message = {}

	if request.method == 'POST':
		if not (request.form.get('recipient') and request.form.get('line1') and request.form.get('city') and request.form.get('country') and request.form.get('warehouse')):
			error_message["address"] = "nimi \"*\" li lon la pana e ona!"

		if not request.form.get('contact'):
			error_message["contact"] = "o pana e nasin toki tawa mi!"

		if sum([int(request.form.get(i)) for i in app.config["LISTING"] if request.form.get(i, "").isnumeric()]) == 0:
			error_message["items"] = "o esun e ijo!"

		warehouse = request.form.get('warehouse')
		if warehouse not in ["US", "ANTE"]:
			error_message["address"] = "ma sina li pakala!"

		for k,v in app.config["LISTING"].items():
			if warehouse == "US":
				available_quantity = available_us.get(k, 0)
			elif warehouse == "ANTE":
				available_quantity = available.get(k, 0)
			if request.form.get(k, "").isnumeric() and int(request.form.get(k)) > available_quantity:
				error_message["items"] = "sina wile e ijo pi lon ala!"

		if request.form.get("mama") != "Sonja" or request.form.get("challenge") != compute_challenge_hash(request.form.get("session_id", ""), request.form.get("sitelen", "")):
			error_message["captcha"] = "sina toki e ijo ike! o toki pona!"

		if not error_message:
			con = connect_database()
			cur = con.cursor()

			cur.execute("SELECT COUNT(*) FROM orders WHERE session_id = ?", (request.form.get("session_id", ""),))
			if cur.fetchone()[0] == 0:
				# Only perform insertation if session_id hasn't been recorded yet
				# Known issue: possible race condition. There's no mutex and this BEGIN TRANSACTION block might happen twice concurrently,
				# which would cause one of them fail to commit/run.
				timenow = round(time.time())
				cur.execute("BEGIN TRANSACTION;")
				cur.execute("""
					INSERT INTO orders(
						session_id, status, warehouse, ip, contact,
						address_recipient, address_phone, address_email,
						address_line1, address_line2, address_line3, address_line4,
						address_city, address_zip, address_country
					) VALUES (?,?,?,?,?,  ?,?,?,  ?,?,?,?,  ?,?,?)
					""",
					(request.form.get("session_id", ""), 0, request.form.get("warehouse", ""), request.remote_addr, request.form.get('contact'),
					request.form.get("recipient", ""), request.form.get("phone", ""), request.form.get("email", ""), 
					request.form.get("line1", ""), request.form.get("line2", ""), request.form.get("line3", ""), request.form.get("line4", ""),
					request.form.get("city", ""), request.form.get("zip", ""), request.form.get("country", ""), )
				)
				if cur.rowcount == 0:
					abort(500)
				order_id = cur.lastrowid

				cur.execute("INSERT INTO status_change(order_id, datetime, status) VALUES (?,?,?)",
					(order_id, timenow, 0,)
				)
				if cur.rowcount == 0:
					abort(500)

				shipping = 0.0
				for k,v in app.config["LISTING"].items():
					if request.form.get(k, "").isnumeric():
						quantity_of_item = int(request.form.get(k, "").isnumeric())
						cur.execute("INSERT INTO inventory_checkout(order_id, item, quantity, price_each) VALUES (?,?,?,?)",
							(order_id, k, quantity_of_item, v["price"],)
						)
						if cur.rowcount == 0:
							abort(500)
						shipping += v["shipping"][warehouse] * quantity_of_item
				cur.execute("INSERT INTO inventory_checkout(order_id, item, quantity, price_each) VALUES (?,?,?,?)",
					(order_id, "pokitawa", 1, shipping,)
				)
				if cur.rowcount == 0:
					abort(500)

				cur.execute("COMMIT TRANSACTION;")
				cur.close()
				con.close()

			# all good! Redirect to /lukin/<token>!
			session_id = request.form.get("session_id", "")
			return redirect(f"/lukin/{session_id}", code=302)

	session_id = str(uuid.uuid4()).replace('-', '')[::-1]
	challenge = compute_challenge_hash(session_id, random.choice(CAPTCHA))
	return render_template('form.html',
			session_id=session_id,
			challenge=challenge,
			error_message=error_message,
			listing=app.config["LISTING"],
			available_us=available_us,
			available=available,
	)

@app.route('/sitelen/<session_id>/<challenge>')
def captcha(session_id, challenge):
	for i in CAPTCHA:
		if challenge == compute_challenge_hash(session_id, i):
			return send_from_directory(os.path.join(app.root_path, "captcha"),
										f"{i}.jpg", mimetype="image/jpeg", download_name="sitelen.jpg")
	abort(404)

@app.route('/lukin/<token>')
def view(token):
	return "<p>Hello, World!</p>"

@app.route('/lawa')
def admin():
	if not check_auth():
		abort(404)
	print(get_available_stock())
	return render_template('admin.html', listing=app.config["LISTING"], stock=get_available_stock())


@app.route('/ante-nanpa-ijo', methods=['POST'])
def update_inventory():
	if not check_auth():
		abort(404)
	con = connect_database()
	cur = con.cursor()
	for key in app.config["LISTING"]:
		update_content = (request.form.get(f"{key}_US"), request.form.get(f"{key}_ANTE"), key)
		cur.execute("UPDATE inventory_list SET quantity_us = ?, quantity_ante = ? WHERE item = ?", update_content)
		if cur.rowcount == 0:
			cur.execute("INSERT INTO inventory_list (quantity_us, quantity_ante, item) VALUES (?,?,?)", update_content)
	cur.close()
	con.close()
	return redirect("/lawa", code=302)


@app.route('/favicon.ico')
def favicon():
	return send_from_directory(os.path.join(app.root_path, "static"),
								"favicon.ico", mimetype="image/vnd.microsoft.icon")

@app.route('/robots.txt')
def robots():
	return send_from_directory(app.root_path, "robots.txt", mimetype="text/plain")
