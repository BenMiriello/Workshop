// Keychain-backed secure storage (auth token, etc.). FREE.

import * as SecureStore from "expo-secure-store";

// Keys are namespaced so they don't collide with anything else in the Keychain.
const prefix = (k: string) => `workshop.${k}`;

export async function get(key: string): Promise<string | null> {
  return SecureStore.getItemAsync(prefix(key));
}

export async function set(key: string, value: string): Promise<void> {
  await SecureStore.setItemAsync(prefix(key), value);
}

export async function remove(key: string): Promise<void> {
  await SecureStore.deleteItemAsync(prefix(key));
}
