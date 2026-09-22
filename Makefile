PYTHON ?= python3
REPO := $(CURDIR)
PREFIX ?= $(HOME)/.local
BINDIR = $(PREFIX)/bin
APPDIR = $(PREFIX)/share/applications
ICONDIR = $(PREFIX)/share/icons/hicolor/scalable/apps

.PHONY: run list apps key install uninstall clean

# Start the graphical remote.
run:
	$(PYTHON) -m remoku

# Print the Roku devices found on the network.
list:
	$(PYTHON) -m remoku --list

# Print the apps and inputs of the last used device.
apps:
	$(PYTHON) -m remoku --apps

# Send a single keypress, e.g. `make key KEY=Home`.
key:
	$(PYTHON) -m remoku --key "$(KEY)"

# Install the `remoku` command (plus a `rokuremote` alias) and a desktop entry.
install:
	install -d "$(BINDIR)" "$(APPDIR)" "$(ICONDIR)"
	ln -sfn "$(REPO)/bin/remoku" "$(BINDIR)/remoku"
	ln -sfn "$(REPO)/bin/remoku" "$(BINDIR)/rokuremote"
	sed "s|@EXEC@|$(REPO)/bin/remoku|" data/remoku.desktop.in \
		> "$(APPDIR)/remoku.desktop"
	install -m644 remoku/assets/remoku.svg "$(ICONDIR)/remoku.svg"
	-update-desktop-database "$(APPDIR)" 2>/dev/null || true
	@echo "Installed: type 'remoku' (or 'rokuremote'), or search 'remoku' in your apps."

uninstall:
	rm -f "$(BINDIR)/remoku" "$(BINDIR)/rokuremote"
	rm -f "$(APPDIR)/remoku.desktop" "$(APPDIR)/roku-remote.desktop"
	rm -f "$(ICONDIR)/remoku.svg" "$(ICONDIR)/roku-remote.svg"
	-update-desktop-database "$(APPDIR)" 2>/dev/null || true
	@echo "Removed the remoku command and desktop entry."

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
