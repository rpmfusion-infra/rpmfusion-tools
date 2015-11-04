#!/bin/bash
check_testing_repo()
{
	pushd "/srv/local_repo/${1}/${2}/updates/testing/${3}/SRPMS/"
	filelist="$(find . -type f -name '*.src.rpm' -ctime +1 | grep -v -e -kmod- -e buildsys-build)"
	[[ ! "${filelist}" ]] && return
	ls -ltr ${filelist}
	echo "./ToStable.py RPMFusion_${2}_${1} ${3}  $(rpm -qp --qf '%{NAME} ' ${filelist} 2> /dev/null)" | sed 's/_el_/_EL_/; s/_fedora_/_Fedora_/;'
	echo
	popd &> /dev/null
	
}

for dist in fedora el ; do
	for repo in free nonfree; do
		if [[ "${dist}" == "fedora" ]]; then
			for release in 21; do
				check_testing_repo ${repo} ${dist} ${release}
			done
		elif  [[ "${dist}" == "el" ]]; then
			for release in 5 6 ; do
                                check_testing_repo ${repo} ${dist} ${release}
                        done
		fi
	done
done
