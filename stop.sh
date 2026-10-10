#!/usr/bin/env bash
cd "$(dirname "$0")"; [ -f server.pid ] && kill "$(cat server.pid)" && rm -f server.pid && echo stopped
