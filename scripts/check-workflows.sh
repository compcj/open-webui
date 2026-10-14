#!/bin/sh
set -eu

actionlint=${1:-actionlint}
for workflow in .github/workflows/*.yml .github/workflows/*.yaml; do
	[ -f "$workflow" ] || continue
	if [ "$workflow" = '.github/workflows/release.yml' ]; then
		# GitHub supports concurrency.queue; actionlint 1.7.12 does not yet.
		# Ignore only this diagnostic in this file, keeping all other checks.
		# https://github.com/rhysd/actionlint/issues/657
		"$actionlint" -shellcheck= -pyflakes= \
			-ignore '^unexpected key "queue" for "concurrency" section\.' "$workflow"
	else
		"$actionlint" -shellcheck= -pyflakes= "$workflow"
	fi
done
