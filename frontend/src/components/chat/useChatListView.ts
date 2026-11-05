import { useCallback, useState } from "react";
import { userStorage } from "../../lib/storage";
import { parseListView, type ChatListView } from "../../pages/chatCard";

// List or grid, remembered on this device. One key for every chat list (the
// Chats page, an app's Chats tab, a framed list), so the layout picked on one
// is the layout found on the next. A view preference, so it commits on change.
const VIEW_KEY = "ws-chats-view";

export function useChatListView(): [ChatListView, (next: ChatListView) => void] {
  const [view, setView] = useState<ChatListView>(() => parseListView(userStorage.local.getItem(VIEW_KEY)));
  const choose = useCallback((next: ChatListView) => {
    setView(next);
    userStorage.local.setItem(VIEW_KEY, next);
  }, []);
  return [view, choose];
}
