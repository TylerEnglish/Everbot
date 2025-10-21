poetry install
poetry run everabot selfcheck   # should report blocked writes
poetry run everabot eval        # runs root test stubs


make docker-build
make docker-run