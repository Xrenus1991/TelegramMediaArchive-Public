#!/usr/bin/env bash
# Install a single forced-command GitHub Actions public key for the monitor account.
# Usage: bash scripts/install_ci_deploy_key.sh 'ssh-ed25519 AAAA... comment'
set -Eeuo pipefail
umask 077
test "$#" -eq 1 || { echo "USAGE: provide one public SSH key"; exit 1; }
key="$1"
case "$key" in
  ssh-ed25519\ *) ;;
  *) echo "KEY_FORMAT_INVALID"; exit 1 ;;
esac
# Strictly accept a normal ed25519 public key, not arbitrary authorized_keys options.
printf '%s\n' "$key" | grep -Eq '^ssh-ed25519 [A-Za-z0-9+/]+={0,2}( [A-Za-z0-9@._+-]+)?$' || {
  echo "PUBLIC_KEY_INVALID"; exit 1;
}
home_ssh="$HOME/.ssh"
mkdir -p "$home_ssh"
chmod 700 "$home_ssh"
auth="$home_ssh/authorized_keys"
touch "$auth"
chmod 600 "$auth"
receiver="/usr/bin/python3 $HOME/TelegramMediaArchive_v1/scripts/ci_receive_deploy.py"
test -f "$HOME/TelegramMediaArchive_v1/scripts/ci_receive_deploy.py" || {
  echo "RECEIVER_NOT_INSTALLED"; exit 1;
}
# The key can run only the receiver, with no forwarding, PTY or other SSH commands.
line="restrict,command=\"$receiver\" $key"
if grep -Fqx -- "$line" "$auth"; then
  echo "CI_DEPLOY_KEY_ALREADY_INSTALLED"
else
  printf '%s\n' "$line" >> "$auth"
  echo "CI_DEPLOY_KEY_INSTALLED"
fi
echo "NOTICE: never add the private key to authorized_keys or a Git repository"
