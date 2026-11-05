# Runs ON THE MAC, fed ahead of macos-build-mac.sh by scripts/macos-build.sh.
#
# Signs the app with one self-signed code-signing identity, kept in its own
# keychain in the build account. An ad hoc signature's designated requirement
# is its cdhash, new with every build, so macOS dropped permissions granted to
# the app (Accessibility, which Cmd+Shift+V needs to post Cmd+V) on each
# rebuild. With the same certificate every build has the same requirement.
#
# The identity is made on first use; its keychain's password is a random file
# readable only by the build account, so signing needs no one at the Mac.
SIGN_NAME="Workshop Local Code Signing"
SIGN_DIR="$HOME/.workshop-signing"
SIGN_KC="$SIGN_DIR/signing.keychain-db"
SIGN_PASS="$SIGN_DIR/keychain-pass"

make_signing_identity() {
  echo "==> Creating the signing identity \"$SIGN_NAME\" in $SIGN_KC"
  mkdir -p "$SIGN_DIR" && chmod 700 "$SIGN_DIR"
  (umask 077 && /usr/bin/openssl rand -hex 32 > "$SIGN_PASS")
  local pass tmp
  pass="$(cat "$SIGN_PASS")"
  tmp="$(mktemp -d)"
  cat > "$tmp/cert.cnf" <<EOF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $SIGN_NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
EOF
  /usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 36500 -config "$tmp/cert.cnf" \
    -keyout "$tmp/key.pem" -out "$tmp/cert.pem" 2>/dev/null
  /usr/bin/openssl pkcs12 -export -inkey "$tmp/key.pem" -in "$tmp/cert.pem" \
    -name "$SIGN_NAME" -out "$tmp/id.p12" -passout "pass:$pass"
  rm -f "$SIGN_KC"
  security create-keychain -p "$pass" "$SIGN_KC"
  security set-keychain-settings "$SIGN_KC"
  security unlock-keychain -p "$pass" "$SIGN_KC"
  security import "$tmp/id.p12" -k "$SIGN_KC" -P "$pass" -T /usr/bin/codesign >/dev/null
  security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$pass" "$SIGN_KC" >/dev/null
  rm -rf "$tmp"
}

# codesign finds an identity only in a keychain on the search list.
search_signing_keychain() {
  local kc list=()
  while read -r kc; do
    kc="${kc//\"/}"
    [ "$kc" = "$SIGN_KC" ] && return 0
    list+=("$kc")
  done < <(security list-keychains -d user)
  security list-keychains -d user -s "$SIGN_KC" ${list[@]+"${list[@]}"}
}

# Prints the identity's SHA-1, making it first if there is none.
signing_identity() {
  [ -f "$SIGN_KC" ] && [ -f "$SIGN_PASS" ] || make_signing_identity >&2
  security unlock-keychain -p "$(cat "$SIGN_PASS")" "$SIGN_KC"
  search_signing_keychain
  security find-certificate -c "$SIGN_NAME" -Z "$SIGN_KC" | awk '/^SHA-1/ { print $NF; exit }'
}

sign_app() {
  local id
  id="$(signing_identity)"
  [ -n "$id" ] || { echo "no signing identity in $SIGN_KC" >&2; return 1; }
  codesign --force --sign "$id" --timestamp=none "$1"
  codesign -dr - "$1" 2>&1 | sed -n 's/^designated => /==> Designated requirement: /p'
}
