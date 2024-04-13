
#find ~/rpmfusion/new/massrebuild -name dead.package > dead.packages
#find ~/rpmfusion/new/massrebuild -name noautobuild >> dead.packages
mount /home/sergio/serjux/mep/webdav
./find_failures.py > /home/sergio/serjux/mep/webdav/serjux/rpms/failed_to_build.html
#./find_failures.py > failed_to_build.html
umount /home/sergio/serjux/mep/webdav
