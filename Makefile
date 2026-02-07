TAILWIND_VERSION := 3.4.17
TAILWIND_CLI := ./tailwindcss
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

ifeq ($(UNAME_S),Darwin)
  ifeq ($(UNAME_M),arm64)
    TAILWIND_PLATFORM := tailwindcss-macos-arm64
  else
    TAILWIND_PLATFORM := tailwindcss-macos-x64
  endif
else
  ifeq ($(UNAME_M),aarch64)
    TAILWIND_PLATFORM := tailwindcss-linux-arm64
  else
    TAILWIND_PLATFORM := tailwindcss-linux-x64
  endif
endif

TAILWIND_URL := https://github.com/tailwindlabs/tailwindcss/releases/download/v$(TAILWIND_VERSION)/$(TAILWIND_PLATFORM)

.PHONY: css css-watch css-setup clean-css

## Download Tailwind standalone CLI (dev only, not committed)
css-setup:
	@if [ ! -f $(TAILWIND_CLI) ]; then \
		echo "Downloading Tailwind CSS v$(TAILWIND_VERSION) CLI..."; \
		curl -sLo $(TAILWIND_CLI) $(TAILWIND_URL) && chmod +x $(TAILWIND_CLI); \
	else \
		echo "Tailwind CLI already present"; \
	fi

## Rebuild compiled CSS (run after changing Tailwind classes in HTML/JS)
css: css-setup
	$(TAILWIND_CLI) -i web/lib/tailwind/input.css -o web/lib/tailwind/styles.css --minify

## Watch mode for development
css-watch: css-setup
	$(TAILWIND_CLI) -i web/lib/tailwind/input.css -o web/lib/tailwind/styles.css --watch

## Remove downloaded CLI binary
clean-css:
	rm -f $(TAILWIND_CLI)
