# LifeOS Apartment: the 3D desktop. Needs SDL2, SDL2_ttf, libcurl and OpenGL (1.x is enough).
CC ?= cc
PKG_CONFIG ?= pkg-config
CFLAGS ?= -O2
CFLAGS += -std=gnu11 -Wall -Wextra -Wno-unused-parameter $(shell $(PKG_CONFIG) --cflags sdl2 SDL2_ttf libcurl)
LDLIBS += $(shell $(PKG_CONFIG) --libs sdl2 SDL2_ttf libcurl) -lGL -lm -lpthread
PREFIX ?= /usr
SRC := $(wildcard src/*.c)

lifeos-apartment: $(SRC) src/app.h
	$(CC) $(CFLAGS) $(LDFLAGS) -o $@ $(SRC) $(LDLIBS)

install: lifeos-apartment
	install -Dm755 lifeos-apartment $(DESTDIR)$(PREFIX)/lib/lifeos/lifeos-apartment-bin

clean:
	rm -f lifeos-apartment

.PHONY: install clean
