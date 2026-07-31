#!/usr/bin/env bash
# Bootstrap a fresh Ubuntu 22.04/24.04 host to run the bot.
# Run once, as the default user (ubuntu), on the new server:
#
#   curl -fsSL https://raw.githubusercontent.com/Dani411110/fashion-affiliate-bot/main/deploy/setup.sh | bash
#
# Then fill in deploy/.env and run: cd ~/fashion-affiliate-bot/deploy && docker compose up -d

set -euo pipefail

REPO_URL="https://github.com/Dani411110/fashion-affiliate-bot.git"
CLONE_DIR="$HOME/fashion-affiliate-bot"

log() { printf '\n\033[1;35m==>\033[0m %s\n' "$*"; }

log "Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
	sudo apt-get update -qq
	sudo apt-get install -y -qq ca-certificates curl git
	sudo install -m 0755 -d /etc/apt/keyrings
	sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
	sudo chmod a+r /etc/apt/keyrings/docker.asc
	echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
		sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
	sudo apt-get update -qq
	sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
	sudo usermod -aG docker "$USER"
else
	echo "Docker already installed, skipping."
fi

# Oracle images ship with a restrictive iptables policy that silently drops 80/443
# even after you open them in the cloud Security List. Both layers must allow it.
log "Opening ports 80 and 443 on the host firewall"
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT || true
if command -v netfilter-persistent >/dev/null 2>&1; then
	sudo netfilter-persistent save
else
	sudo apt-get install -y -qq iptables-persistent
fi

log "Adding swap (the ARM box has plenty of RAM, but Chromium spikes)"
if [ ! -f /swapfile ]; then
	sudo fallocate -l 2G /swapfile
	sudo chmod 600 /swapfile
	sudo mkswap /swapfile
	sudo swapon /swapfile
	echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

log "Cloning the repository"
if [ -d "$CLONE_DIR/.git" ]; then
	git -C "$CLONE_DIR" pull --ff-only
else
	git clone "$REPO_URL" "$CLONE_DIR"
fi

log "Enabling Docker on boot"
sudo systemctl enable --now docker

cat <<'DONE'

Done. Remaining steps:

  1. cd ~/fashion-affiliate-bot/deploy
  2. cp .env.example .env && nano .env      # paste the variables, set SITE_DOMAIN
  3. docker compose up -d --build           # first build takes ~10 min (Chromium)
  4. docker compose logs -f bot

If the docker command says "permission denied", log out and back in once so the
new group membership takes effect.

DONE
