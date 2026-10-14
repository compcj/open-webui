#!/bin/sh
set -eu

# Each install owns its cache; only retry npm's transient cache-file collisions.
cache_dir=
trap '[ -z "$cache_dir" ] || rm -rf "$cache_dir"' 0
trap 'exit 130' INT
trap 'exit 143' TERM

attempt=1
while [ "$attempt" -le 3 ]; do
	cache_dir=$(mktemp -d /tmp/open-webui-npm.XXXXXX)
	status=0
	npm ci --force --cache "$cache_dir" > "$cache_dir/install.log" 2>&1 || status=$?
	cat "$cache_dir/install.log"
	if [ "$status" -eq 0 ]; then
		exit 0
	fi
	if [ "$attempt" -eq 3 ] ||
		! grep -Eq 'npm (error|ERR!) code EEXIST' "$cache_dir/install.log" ||
		! grep -Fq '/_cacache/' "$cache_dir/install.log"; then
		exit "$status"
	fi
	printf 'npm cache collision on attempt %s/3; retrying with a fresh cache\n' "$attempt" >&2
	rm -rf "$cache_dir"
	cache_dir=
	sleep "$attempt"
	attempt=$((attempt + 1))
done
