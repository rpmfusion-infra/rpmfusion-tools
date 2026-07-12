#!/usr/bin/python

import sys
import os
import koji

session = koji.ClientSession('https://koji.rpmfusion.org/kojihub')
session.ssl_login(os.path.expanduser('~/.rpmfusion.cert'), None, None)

if len(sys.argv) != 2:
    print(f"Usage: {os.path.basename(sys.argv[0])} <package_name>")
    sys.exit(1)

pkg_name = sys.argv[1]

print(f"\n=== {pkg_name} ===")

# Step 3: Get all tags in Koji for this package
all_tags = session.listPackages(pkgID=pkg_name, inherited=True, with_dups=True)
# Step 4: Find tags not matching any remote branch
for t in list(reversed(all_tags))[:2]:
    tag_name = t['tag_name']
    # Never remove from trashcan tag
    if tag_name == 'trashcan':
        print("    Skipping trashcan tag.")
        continue

    confirm = input(f"    Remove '{pkg_name}' from tag {tag_name}? [ y / n ]: ")
    if confirm.lower() != 'y':
        print("    Skipped.")
        continue

    try:
        if t.get('blocked'):
            print(f"    Unblocked from '{tag_name}'")
            session.packageListUnblock(tag_name, pkg_name)
        print(f"    Removed from '{tag_name}'")
        session.packageListRemove(tag_name, pkg_name, force=True)
    except Exception as e:
        print(f"    ERROR: {e}")

