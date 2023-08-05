find ~/rpmfusion/new/massrebuild -name dead.package > dead.packages
./find_failures.py > failed_to_build.html
