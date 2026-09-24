.PHONY: test test-full check demo
test:
	PYTHONPATH=src python3 -m pytest -q -m 'not slow'
test-full:
	PYTHONPATH=src python3 -m pytest -q
check:
	python3 -m compileall -q src
demo:
	PYTHONPATH=src python3 scripts/run_demo.py

.PHONY: test-dsh-plugin test-dsh-profile
test-dsh-plugin:
	node --test tests/dsh-profile/*.test.mjs
test-dsh-profile:
	python3 tests/dsh-profile/run.py --dsh-root "$(DSH_ROOT)"
