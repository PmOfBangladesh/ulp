import logging

from telethon.tl.types import (
    KeyboardButtonUrl,
    KeyboardButtonCallback,
    KeyboardButtonWebView,
    KeyboardButtonSwitchInline,
    KeyboardButtonUrlAuth,
    KeyboardButtonCopy,
    KeyboardButtonRow,
    KeyboardButtonRequestPeer,
    KeyboardButtonRequestPhone,
    KeyboardButtonRequestGeoLocation,
    KeyboardButtonGame,
    KeyboardButtonBuy,
    KeyboardButtonSimpleWebView,
    InputKeyboardButtonUserProfile,
    ReplyInlineMarkup,
)

LOGGER = logging.getLogger(__name__)


class SmartButtons:
    def __init__(self):
        self._button = []
        self._header_button = []
        self._footer_button = []
        self._styles = {}

    def button(self, text, callback_data=None, url=None, pay=None, web_app=None,
               login_url=None, switch_inline_query=None,
               switch_inline_query_current_chat=None,
               switch_inline_query_chosen_chat=None, copy_text=None,
               callback_game=None, request_peer=None, request_phone=None,
               request_location=None, simple_web_view=None, user_profile=None,
               style=None, position=None):
        try:
            if callback_data is not None:
                encoded = callback_data.encode() if isinstance(callback_data, str) else callback_data
                btn = KeyboardButtonCallback(text=text, data=encoded)
            elif url is not None:
                btn = KeyboardButtonUrl(text=text, url=url)
            elif pay:
                btn = KeyboardButtonBuy(text=text)
            elif web_app is not None:
                u = web_app.url if hasattr(web_app, 'url') else web_app
                btn = KeyboardButtonWebView(text=text, url=u)
            elif simple_web_view is not None:
                btn = KeyboardButtonSimpleWebView(text=text, url=simple_web_view)
            elif login_url is not None:
                if isinstance(login_url, dict):
                    btn = KeyboardButtonUrlAuth(text=text, **login_url)
                else:
                    btn = KeyboardButtonUrlAuth(text=text, url=login_url, button_id=0)
            elif switch_inline_query is not None:
                btn = KeyboardButtonSwitchInline(text=text, query=switch_inline_query, same_peer=False)
            elif switch_inline_query_current_chat is not None:
                btn = KeyboardButtonSwitchInline(text=text, query=switch_inline_query_current_chat, same_peer=True)
            elif switch_inline_query_chosen_chat is not None:
                q = getattr(switch_inline_query_chosen_chat, 'query', str(switch_inline_query_chosen_chat))
                pt = getattr(switch_inline_query_chosen_chat, 'peer_types', None)
                btn = KeyboardButtonSwitchInline(text=text, query=q, same_peer=False, peer_types=pt)
            elif copy_text is not None:
                v = copy_text.text if hasattr(copy_text, 'text') else str(copy_text)
                btn = KeyboardButtonCopy(text, v)
            elif callback_game:
                btn = KeyboardButtonGame(text=text)
            elif request_peer is not None:
                if isinstance(request_peer, dict):
                    btn = KeyboardButtonRequestPeer(text=text, **request_peer)
                else:
                    btn = KeyboardButtonRequestPeer(
                        text=text,
                        button_id=request_peer.button_id,
                        peer_type=request_peer.peer_type,
                        max_quantity=getattr(request_peer, 'max_quantity', 1),
                    )
            elif user_profile is not None:
                btn = user_profile if isinstance(user_profile, InputKeyboardButtonUserProfile) else InputKeyboardButtonUserProfile(text, user_profile)
            elif request_phone:
                btn = KeyboardButtonRequestPhone(text=text)
            elif request_location:
                btn = KeyboardButtonRequestGeoLocation(text=text)
            else:
                btn = KeyboardButtonCallback(text=text, data=b'')
        except Exception as e:
            LOGGER.error(f"Failed to create button: {e}")
            raise

        if style:
            self._styles[id(btn)] = style

        if not position:
            self._button.append(btn)
        elif position == "header":
            self._header_button.append(btn)
        elif position == "footer":
            self._footer_button.append(btn)

    def build_menu(self, b_cols=1, h_cols=8, f_cols=8):
        menu = [self._button[i:i + b_cols] for i in range(0, len(self._button), b_cols)]
        if self._header_button:
            if len(self._header_button) > h_cols:
                for i in range(0, len(self._header_button), h_cols):
                    menu.insert(0, self._header_button[i:i + h_cols])
            else:
                menu.insert(0, self._header_button)
        if self._footer_button:
            if len(self._footer_button) > f_cols:
                for i in range(0, len(self._footer_button), f_cols):
                    menu.append(self._footer_button[i:i + f_cols])
            else:
                menu.append(self._footer_button)
        return ReplyInlineMarkup(rows=[KeyboardButtonRow(buttons=row) for row in menu])

    def has_styles(self):
        return bool(self._styles)

    def build_botapi_markup(self, b_cols=1, h_cols=8, f_cols=8):
        """Bot API inline_keyboard (with colored styles)."""
        def conv(btn):
            b = {"text": getattr(btn, "text", "") or ""}
            data = getattr(btn, "data", None)
            url = getattr(btn, "url", None)
            if data:
                try:
                    b["callback_data"] = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
                except Exception:
                    b["callback_data"] = "cb"
            elif url:
                b["url"] = url
            else:
                b["callback_data"] = "cb"
            st = self._styles.get(id(btn))
            if st:
                b["style"] = st
            return b

        menu = [[conv(x) for x in self._button[i:i + b_cols]]
                for i in range(0, len(self._button), b_cols)]
        if self._header_button:
            if len(self._header_button) > h_cols:
                for i in range(0, len(self._header_button), h_cols):
                    menu.insert(0, [conv(x) for x in self._header_button[i:i + h_cols]])
            else:
                menu.insert(0, [conv(x) for x in self._header_button])
        if self._footer_button:
            if len(self._footer_button) > f_cols:
                for i in range(0, len(self._footer_button), f_cols):
                    menu.append([conv(x) for x in self._footer_button[i:i + f_cols]])
            else:
                menu.append([conv(x) for x in self._footer_button])
        return menu


# ─────────────────────────────────────────────────────────────
#  Contextual button colors (Bot API: "primary" | "success" | "danger")
# ─────────────────────────────────────────────────────────────
STYLE_MAP = {
    # navigation / info  -> primary (blue)
    "main_menu": "primary",
    "about": "primary",
    "policy": "primary",
    "back_to_start": "primary",
    # actions -> success (green)
    "exfmt:mailpass": "success",
    "exfmt:userpass": "success",
    "exfmt:num_pass": "success",
    "exfmt:domain": "success",
    "exfmt:url": "success",
    "cmbfmt:mailpass": "success",
    "cmbfmt:userpass": "success",
    "cmbfmt:num_pass": "success",
    # destructive -> danger (red)
    "exfmt:cancel": "danger",
    "cmbfmt:cancel": "danger",
    "dbclean:data": "danger",
    "dbclean:downloads": "danger",
}
# prefix rules (navigation families)
_PREFIX_STYLES = (
    ("dbpg:", "primary"),
    ("logpg:", "primary"),
)


def style_for(btn):
    """Resolve the color style for a Telethon inline button."""
    st = getattr(btn, "_style", None)
    if st:
        return st
    data = getattr(btn, "data", None)
    if data:
        try:
            d = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
        except Exception:
            d = ""
        if d in STYLE_MAP:
            return STYLE_MAP[d]
        for pref, sty in _PREFIX_STYLES:
            if d.startswith(pref):
                return sty
        return None
    if getattr(btn, "url", None):
        return "primary"
    return None


def _iter_rows(markup):
    """Yield button rows from either a ReplyInlineMarkup or a raw list."""
    if markup is None:
        return
    if hasattr(markup, "rows"):
        for row in markup.rows:
            yield list(getattr(row, "buttons", []) or [])
    elif isinstance(markup, (list, tuple)):
        for row in markup:
            if isinstance(row, (list, tuple)):
                yield list(row)
            else:
                yield [row]


def _btn_to_api(btn):
    item = {"text": getattr(btn, "text", "") or ""}
    data = getattr(btn, "data", None)
    url = getattr(btn, "url", None)
    if data:
        try:
            item["callback_data"] = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
        except Exception:
            item["callback_data"] = "cb"
    elif url:
        item["url"] = url
    else:
        item["callback_data"] = "cb"
    st = style_for(btn)
    if st:
        item["style"] = st
    return item


def markup_has_styles(markup):
    for row in _iter_rows(markup):
        for b in row:
            if style_for(b):
                return True
    return False


def markup_to_botapi(markup):
    """Convert any Telethon inline markup (or raw row list) to Bot API JSON with colors."""
    return [[_btn_to_api(b) for b in row] for row in _iter_rows(markup)]

    def reset(self):
        self._button = []
        self._header_button = []
        self._footer_button = []
