#!/usr/bin/bash

./find_failures.py > failed_to_build.html

mount /home/sergio/serjux/mep/webdav
mv failed_to_build.html /home/sergio/serjux/mep/webdav/serjux/rpms/failed_to_build.html
sleep 2
umount /home/sergio/serjux/mep/webdav
