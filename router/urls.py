"""Shared HTTP(S) normalization; never turns a request into a shell command."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit

WEBSITES = {
    'youtube': 'https://www.youtube.com', 'google': 'https://www.google.com',
    'github': 'https://github.com', 'reddit': 'https://www.reddit.com',
    'wikipedia': 'https://www.wikipedia.org', 'gmail': 'https://mail.google.com',
    'netflix': 'https://www.netflix.com', 'amazon': 'https://www.amazon.com',
    'twitch': 'https://www.twitch.tv', 'chatgpt': 'https://chatgpt.com',
    'stackoverflow': 'https://stackoverflow.com', 'stack overflow': 'https://stackoverflow.com',
}


def normalize_url(value):
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Website addresses cannot contain control characters.')
    value = value.strip()
    value = WEBSITES.get(value.casefold(), value)
    if not value or any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value:
        raise ValueError('Use a valid HTTP/HTTPS address without spaces or control characters.')
    if '://' not in value:
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', value) and not re.match(r'^[\w.-]+:\d+(?:/|$)', value):
            raise ValueError('Only HTTP/HTTPS websites are supported.')
        value = 'https://' + value
    parsed = urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError('Only HTTP/HTTPS websites without credentials are allowed.')
    _ = parsed.port
    host = parsed.hostname
    try:
        ipaddress.ip_address(host)
        encoded_host = host
    except ValueError:
        encoded_host = host.encode('idna').decode('ascii')
        labels = encoded_host.rstrip('.').split('.')
        if host != 'localhost' and len(labels) < 2:
            raise ValueError('Supply a full website domain, such as example.org.')
        if len(encoded_host) > 253 or any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in labels):
            raise ValueError('Invalid website hostname.')
    netloc = f'[{encoded_host}]' if ':' in encoded_host else encoded_host
    if parsed.port is not None:
        netloc += f':{parsed.port}'
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
