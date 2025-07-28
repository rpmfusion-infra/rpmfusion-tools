#!/usr/bin/bash

./find_failures.py > failed_to_build.html
scp failed_to_build.html server:/var/www/vhosts/serjux.com/httpdocs/rpms/failed_to_build.html
