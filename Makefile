.PHONY: install run eval selfcheck docker-build docker-run root-check

install:
	poetry install

run:
	poetry run everabot --help

eval:
	poetry run everabot eval

selfcheck:
	poetry run everabot selfcheck

root-check:
	poetry run python test/run_root_tests.py

docker-build:
	docker build -t everabot:dev .

docker-run:
	docker run --rm -it \
	  -v "$$PWD/ruleset:/workspace/ruleset:ro" \
	  -v "$$PWD/test:/workspace/test:ro" \
	  -v "$$PWD/snapshots:/workspace/snapshots" \
	  everabot:dev eval
