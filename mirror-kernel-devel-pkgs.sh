#!/bin/bash
THISSCRIPTSNAME=mirror-kernel-devel-pkgs
LOCKFILE="${HOME}/.${THISSCRIPTSNAME}.lock"
REMINDMECHANCE=50
localrepo=/srv/kernel-devel-pkgs/fedora/

latest_kernel_package()
{
	local release="${1%%/}"

	if [[ ! -d "${localrepo}/${release}/" ]] ; then
		mkdir "${localrepo}/${release}/"
	fi

        if [[ "${release}" == "development" ]]; then
		# f11-alpha f11-beta f11-preview
                kojitags="f21 f21-updates f21-updates-testing f21-updates-testing-pending"
        elif [[ "${release}" == "22" ]]; then
                kojitags="f22 f22-updates f22-updates-testing f22-updates-testing-pending"
        elif [[ "${release}" == "20" ]]; then
                kojitags="f20 f20-updates f20-updates-testing f20-updates-testing-pending"
        elif [[ "${release}" == "19" ]]; then
                kojitags="f19 f19-updates f19-updates-testing f19-updates-testing-pending"
        elif [[ "${release}" == "18" ]]; then
                kojitags="f18 f18-updates f18-updates-testing f18-updates-testing-pending"
	elif [[ "${release}" == "17" ]]; then
	        kojitags="f17 f17-updates f17-updates-testing f17-updates-testing-pending"
        elif [[ "${release}" == "16" ]]; then
                kojitags="f16 f16-updates f16-updates-testing f16-updates-testing-pending"
        elif [[ "${release}" == "15" ]]; then
                kojitags="dist-f15 dist-f15-updates dist-f15-updates-testing dist-f15-updates-candidate dist-f15-updates-testing-pending"
        elif [[ "${release}" == "14" ]]; then
                kojitags="dist-f14 dist-f14-updates dist-f14-updates-testing"
        elif [[ "${release}" == "13" ]]; then
                kojitags="dist-f13 dist-f13-updates dist-f13-updates-testing"
        elif [[ "${release}" == "12" ]]; then
                kojitags="dist-f12 dist-f12-updates dist-f12-updates-testing dist-f12-updates-candidate"
        elif [[ "${release}" == "11" ]]; then
                kojitags="dist-f11 dist-f11-updates dist-f11-updates-testing dist-f11-updates-candidate"
        elif [[ "${release}" == "10" ]]; then
                kojitags="dist-f10 dist-f10-updates dist-f10-updates-testing dist-f10-updates-candidate"
        elif [[ "${release}" == "9" ]]; then
                kojitags="dist-f9 dist-f9-updates dist-f9-updates-testing dist-f9-updates-candidate"
	fi

	for kojitag in ${kojitags}; do
		for kernelvariant in kernel; do
			for iteration in one two; do
				tmpfile="$(mktemp -t download-kdevs.XXXXXXXXX)"
				koji latest-pkg --quiet ${kojitag} ${kernelvariant} &> "${tmpfile}" &
				local backgroudpid=$!
				local backgroudpcomm="$(ps --noheading -o comm -p ${backgroudpid})"
				for waittime in 2 3 5 30; do
					sleep ${waittime}
					local backgroundprocess="$(ps --noheading -o comm -p ${backgroudpid})"
					if [[ "${backgroundprocess}" != "${backgroudpcomm}" ]] || [[ ! "${backgroudpcomm}" ]]  ; then
						# seemes command succeed; leave both loops
						break 2
					fi
 
					if (( ${waittime} == 30 )); then
						# seemes command still around; kill it
						kill ${backgroudpid}
						# echo killed ${backgroudpid}
		
						if [[ "${iteration}" == "two" ]] ; then
							# seemes it didn't work both times; give up
							echo "Could not get data from koji" 
							continue 3
						fi
					fi
				done	
			done

			latestkernelaccordingtokoji="$(awk '{print $1}' < "${tmpfile}")"
			if grep -i -e Error -e killed < "${tmpfile}" > /dev/null ; then
				# seems tag does not exist; skip
				continue
			elif [[ ! "${latestkernelaccordingtokoji}" ]] || [[ "${latestkernelaccordingtokoji}" ==  "${latestkernelaccordingtokoji##kernel}"  ]] ; then
				# echo "Koji return bogus looking data: ${latestkernelaccordingtokoji} "
				continue
			fi
			rm "${tmpfile}"
			# echo "$(date) ${kojitag} ${latestkernelaccordingtokoji}"
	
			# download if needed
			download_kernel_devel_packages ${latestkernelaccordingtokoji} ${localrepo}/${release}/ ${kojitag} ${kernelvariant}

			listofkernels="${listofkernels} -e ${latestkernelaccordingtokoji}"
		done		
	done

	if [[ "${somethingwasdownloaded}" ]]; then
		# cleanup
	        if pushd ${localrepo}/${release}/ > /dev/null ; then
		        for i in $(ls kernel/ | grep -v ${listofkernels}); do
                        	rm -rf ${localrepo}/${release}/kernel/${i}
                	done
        	        popd > /dev/null
	        fi

		createrepo "${localrepo}/${release}/" > /dev/null
		somethingwasdownloaded=""
	fi
}

download_kernel_devel_packages()
{

	local kernelver="${1}"
	local targetdir="${2%%/}"
	local kojitag="${3}"
	local kernelvariant="${4}"


	# sanity check
	if [[ ! -d "${targetdir}" ]]; then
		echo download_kernel-devel_packages ${1} ${2} ${3} ${4}
		echo "${targetdir} not found; skipping"
		return 1
	fi

	bulk="${kernelver}"
	while [[ "${bulk}" != "${krelease}" ]]; do
		kversion="${krelease}"
		krelease="$(echo ${bulk} | cut -d '-' -f 1)"
		bulk="$(echo ${bulk} | cut -d '-' -f 2-)"
	done
	unset bulk

	if [[ -d "${targetdir}/${kernelvariant}/${kernelver}" ]]; then
		# seems we have the kernel already in the proper place; skip
		return 0
	fi

	# seems we need to download it
	echo "$(date) Downloading ${kojitag} ${kernelver}"
	lftp  -c "open http://kojipkgs.fedoraproject.org/packages/ ; mirror --parallel=2  -c -e --include 'kernel-(|.*-)devel' --exclude '.*debug.*' --exclude 'data'  /packages/${kernelvariant}/${kversion}/${krelease}/ ${targetdir}/${kernelvariant}/${kernelver} ; exit"
	somethingwasdownloaded="foo"
	# echo "$(date) Downloaded ${kojitag} ${kernelver}"
}


# Are we running already?
if ! lockfile -r 1 "${LOCKFILE}" ; then
        if ps --no-heading "$(cat "${LOCKFILE}")" &> /dev/null
        then
                echo "Existing lockfile and process seems to be still around. Aborting." >&2
                exit 1
        else
                # no process with that pid
                # FIXME -- there are better ways to deal with this case; it could be another process
                rm -f "${LOCKFILE}"
		lockfile -r 1 "${LOCKFILE}"
        fi
fi
chmod +w "${LOCKFILE}"
echo $$ > "${LOCKFILE}"

# go
for release in 20 22 development ; do
	latest_kernel_package ${release}
done 
# &> ${HOME}/logs/mirror-$(date +%s)

rm -f "${LOCKFILE}"

if [[ ${REMINDMECHANCE} > 0 ]] && (( $((${RANDOM}%${REMINDMECHANCE})) == 0 ))
then
        echo "Hi,"$'\n'$'\n'"this is ${0} on ${HOSTNAME} running for ${USERNAME} aka ${USER}. I'm a 
script called from a cron job regularly and now and then use the opportunity to remind you that I'm 
still here and working hard for you. Thanks for reading this and have a great day. "$'\n'$'\n'"Yours
 sincerely,"$'\n'$'\t'"$(basename ${0})" | fmt -w 76
fi

