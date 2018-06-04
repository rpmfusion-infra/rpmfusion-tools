#!/bin/bash
# configuration
LISTOFPKGS="audacious audacity chromium kernel libmicrohttpd libva libcec platform gstreamer gstreamer-plugins-base gstreamer-plugins-good gstreamer-plugins-bad-free qmmp libbluray"
LISTOFDISTS="f28-updates f28-updates-testing f27-updates f27-updates-testing"

# basics
THISSCRIPTSNAME=$(basename ${0})
LOCKFILE="${HOME}/.${THISSCRIPTSNAME}.lock"
DATADIR="${HOME}/.${THISSCRIPTSNAME}"

# koji alive?
if ! koji list-tags &> /dev/null ; then
	exit 1
fi

# Are we running already?
if ! lockfile -r 1 "${LOCKFILE}" ; then
        if ps --no-heading "$(cat "${LOCKFILE}")" &> /dev/null
        then
                echo "Existing lockfile and process seems to be still around. Aborting." >&2
                exit 1
        else
                # no process with that pid
                rm -f "${LOCKFILE}"
				lockfile -r 1 "${LOCKFILE}" 
        fi
fi
chmod +w "${LOCKFILE}"
echo $$ > "${LOCKFILE}"

# does out setting dir exist
if [[ ! -d ${DATADIR} ]]; then
	if ! mkdir -p "${DATADIR}" ; then 
         echo "Could not create ${DATADIR}. Aborting." >&2
         exit 1
	fi
fi

# go
i=0
for tag in ${LISTOFDISTS}; do
	if ! koji latest-pkg --quiet ${tag} ${LISTOFPKGS} > "${DATADIR}/${tag}.new"; then
		echo "koji failed when running 'koji latest-pkg --quiet ${tag} ${LISTOFPKGS}'"
		continue
	fi
	
	if [[ -e "${DATADIR}/${tag}" ]]; then
		if ! (cd ${DATADIR}; diff -u "${tag}" "${tag}.new"); then
			echo $'\n'
		fi
	fi
	mv -f "${DATADIR}/${tag}.new" "${DATADIR}/${tag}"
done

# cleanup
rm -f "${LOCKFILE}"

# reminder
if [[ ${REMINDMECHANCE} > 0 ]] && (( $((${RANDOM}%${REMINDMECHANCE})) == 0 ))
then
        echo "Hi,"$'\n'$'\n'"this is ${0} on ${HOSTNAME} running for ${USERNAME} aka ${USER}. I'm a 
script called from a cron job regularly and now and then use the opportunity to remind you that I'm 
still here and working hard for you. Thanks for reading this and have a great day. "$'\n'$'\n'"Yours
 sincerely,"$'\n'$'\t'"$(basename ${0})" | fmt -w 76
fi

