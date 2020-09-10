PREFIX	?= /usr/local

MANDIR	= $(addprefix $(DESTDIR),$(PREFIX)/share/man/man8)
CONFDIR	= $(addprefix $(DESTDIR),/etc/dnf/plugins)

ifeq (, $(PYTHONSITES))
PYTHON = $(shell command -v python3)
ifeq (, $(PYTHON))
$(error "No python3 installation found.")
endif

PY_MAJOR = $(basename $(lastword $(shell $(PYTHON) --version)))
PYTHON_PREFIX = $(dir $(subst /bin/,/bin,$(dir $(PYTHON))))

PYTHONSITES = $(addsuffix lib/python$(PY_MAJOR)/site-packages,$(PYTHON_PREFIX))
endif

DNFPLUGINDIR = $(addprefix $(DESTDIR),$(PYTHONSITES)/dnf-plugins)

TARGETS = kpatch.py conf/kpatch.conf man/dnf.kpatch.8.gz

all: $(TARGETS)

install: $(TARGETS)
	install -d $(MANDIR)
	install man/dnf.kpatch.8.gz $(MANDIR)
	install -d $(CONFDIR)
	install conf/kpatch.conf $(CONFDIR)
	install -d $(DNFPLUGINDIR)
	install kpatch.py $(DNFPLUGINDIR)

%.gz: %
	gzip --keep $^
