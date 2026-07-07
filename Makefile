.PHONY: test lint fmt e2e demo

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff check --fix .

e2e:
	bash e2e/run.sh

demo:
	@for tool in vhs ttyd ffmpeg; do \
		command -v $$tool >/dev/null 2>&1 || { \
			echo "error: '$$tool' is required to render the demo but is not installed."; \
			echo "  vhs:    https://github.com/charmbracelet/vhs#installation"; \
			echo "  ttyd:   https://github.com/tsl0922/ttyd/releases (static binary)"; \
			echo "  ffmpeg: your package manager, or https://ffmpeg.org/download.html"; \
			exit 1; }; \
	done
	bash demo/setup.sh
	vhs demo/demo.tape
