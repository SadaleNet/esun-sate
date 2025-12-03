#!/bin/bash

cd ~/esunsate/src/
while true; do
        echo "Starting server... $(date)" | tee -a ../data/esunsate.log 2&>1
        uwsgi --plugin python3 --http-socket 127.0.0.1:3276 --master -p 4 -w app:app --touch-reload=../touch-reload | tee -a ../data/esunsate.log 2&>1
        sleep 60;
done;
