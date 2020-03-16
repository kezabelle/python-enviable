# -*- coding: utf-8 -*-
"""
a small module for wrapping over environment variables (pulled from os.environ)
which provides convenience methods to fetch and check various data types
(including iterables) in what I'd charitably hope is a sensible way.

Explicitly doesn't attempt to read from any .env or .envrc file, because that
doesn't describe valid examples or which things may/should be set into the
environment. It becomes an absolute pot-luck.

Tracks requested environment variables and their default/fallback/example values, and
whether or not the fallback was used. Never tracks the actual environment value.

If this package isn't to your liking, there's **plenty** of others, and I'm
largely suffering from Not-Invented-Here syndrome.

All methods exposed by the Environment accept a key and a default.
The key is the environment variable to search for.
The default MUST be a string, as it is subject to the same parsing as if it had
been found in the environment, and thus serves as a documented example of a valid
value to export as an environment variable. Enforced value documentation!

A short series of examples follow; see the README.rst file for a fuller explanation::

    from enviable import env
    DEBUG = env.bool("DEBUG", "off")
    GIT_HASH = env.hex("COMMIT_REF", "11ff3fe8ccfa4bbd9c144f68b84c80f6")
    SERVER_EMAIL = env.email("DEFAULT_EMAIL", "a@b.com")
    VALID_OPTION = env.one_of("VAR_NAME", "3", choices="1,2,3,4")
    DYANMIC_IMPORT = env.importable("MY_MODULE", "path.to.mymodule")
    LOCAL_FILE = env.filepath("ACCESS_KEYS", "/valid/path/to/keys.json")
    API_URL = env.web_address("API_URL", "https://example.com/")
    NUMBERS = env.tuple("NUMBERS", "(12,3,456)", converter=env.ensure.int)
    UNORDERED_NUMBERS = env.frozenset("NUMBERS", "12, 3, 456", converter=env.ensure.int)

Failing to successfully convert (or just validate) the value immediately halts execution by raising
`EnvironmentCastError` which is a subclass of `ValueError` - it is recommended
that you only catch the former.

Should be able to handle the following:
- text
- integer
- boolean
- uuid (with and without hyphens)
- email (checks the string is email-like. Does not fully parse/validate, because that's a fool's errand)
- hex (validates the string)
- base64 encoded data (validates it decodes)
- decimal
- importable python paths (validates the string)
- file paths (validates the file exists and is readable)
- directories (validates the directory exists)
- URLs (sanity-checks the string ... ish)
- tuples/lists/sets/frozensets of any of the above
- dictionaries
- json

If Django is installed (sorry, I'm lazy) it should also handle:
- datetime
- date
- time

Running this file directly (`python enviable.py`) should execute a small test suite
which ought to pass. Please open an ticket if it doesn't.
"""
from __future__ import absolute_import, unicode_literals

import base64
import binascii
import decimal
import functools
import itertools
import json
import logging
import operator
import os
import re
import string
import tokenize
import uuid
import datetime as dt
from typing import (
    Text,
    Union,
    Set,
    Optional,
    Callable,
    Iterable,
    Any,
    Tuple,
    List,
    FrozenSet,
    Mapping,
    Iterator,
    Dict,
)

try:
    from six import string_types
except ImportError:
    string_types = (str,)

try:
    from django.utils.dateparse import parse_date, parse_datetime, parse_time
    from django.utils.timezone import utc

    CAN_PARSE_TEMPORAL = True
except ImportError:

    def temporal_failure(v):  # type: ignore
        raise NotImplementedError(
            "I've not implemented parsing of dates/datetimes/times without depending on Django, sorry chum"
        )

    parse_date = temporal_failure
    parse_datetime = temporal_failure
    parse_time = temporal_failure
    utc = None
    CAN_PARSE_TEMPORAL = False


__version_info__ = "0.2.1"
__version__ = "0.2.1"
version = "0.2.1"
VERSION = "0.2.1"


def get_version():
    # type: () -> Text
    return version


__all__ = [
    "EnvironmentCastError",
    "EnvironmentDefaultError",
    "Environment",
    "get_version",
    "env",
]


logger = logging.getLogger(__name__)


class EnvironmentCastError(ValueError):
    """
    Raised by EnvironmentCaster when one of the utility methods cannot proceed
    with the incoming data.
    """


class EnvironmentDefaultError(ValueError):
    """
    Raised by Environment when a default value is provided and it's not
    a stringy example.
    """


class EnvironmentCaster(object):
    """
    Provides utilities to cast a raw string value to a more appropriate type.

    Each method accepts a single stringy value, so that the method may be used
    on iterables etc.
    """

    __slots__ = ()

    def int(self, value):
        # type: (Text) -> int
        try:
            return int(value)
        except ValueError as e:
            raise EnvironmentCastError(str(e))

    def boolean(self, value):
        # type: (Text) -> bool
        value = value.lower().strip()
        good_values = ("true", "on", "y", "yes", "1")
        bad_values = ("false", "off", "n", "no", "0", "")
        if value in good_values:
            return True
        elif value in bad_values:
            return False
        zipped_up = zip(good_values, bad_values)
        options = ("/".join(x) for x in zipped_up)
        paired = ", ".join(options)
        raise EnvironmentCastError(
            "Failed to read as a boolean. Got value {0!r}. Expected one of: {1!s}".format(
                value, paired
            )
        )

    bool = boolean

    def uuid(self, value):
        # type: (Text) -> uuid.UUID
        value = value.lower().strip()
        try:
            return uuid.UUID(value)
        except ValueError:
            raise EnvironmentCastError(
                "Cannot create uuid from unrecognised value {0!r}".format(value)
            )

    def datetime(self, value):
        # type: (Text) -> dt.datetime
        parsed_value = parse_datetime(value)  # type: Optional[dt.datetime]
        if parsed_value is not None:
            return parsed_value
        del parsed_value
        try:
            return dt.datetime.strptime(value, "%Y-%m-%d")
        except ValueError as e:
            raise EnvironmentCastError(
                "Cannot create datetime from unrecognised value {0!r}, {1!s}".format(
                    value, e
                )
            )

    def date(self, value):
        # type: (Text) -> dt.date
        try:
            parsed_value = parse_date(value)  # type: Optional[dt.date]
        except ValueError as e:
            raise EnvironmentCastError(
                "Cannot create date from unrecognised value {0!r}, {1!s}".format(
                    value, e
                )
            )
        if parsed_value is not None:
            return parsed_value
        raise EnvironmentCastError(
            "Could not parse value {0!r} into a datetime.date".format(value)
        )

    def time(self, value):
        # type: (Text) -> dt.time
        parsed_value = parse_time(value)  # type: Optional[dt.time]
        if parsed_value is not None:
            return parsed_value
        raise EnvironmentCastError(
            "Could not parse value {0!r} into a datetime.time".format(value)
        )

    def email(self, value):
        # type: (Text) -> Text
        if "@" not in value:
            raise EnvironmentCastError(
                "Could not parse value {0!r} as an email address; missing @".format(
                    value
                )
            )
        if value.count("@") > 1:
            raise EnvironmentCastError(
                "Could not parse value {0!r} as an email address; multiple @ symbols".format(
                    value
                )
            )
        if len(value) < 3:
            raise EnvironmentCastError(
                "Could not parse value {0!r} as an email address; should be at least a@b, right?".format(
                    value
                )
            )
        if value[0] == "@" or value[-1] == "@":
            raise EnvironmentCastError(
                "Could not parse value {0!r} as an email address; starts or ends with @".format(
                    value
                )
            )
        return value

    def hex(self, value):
        # type: (Text) -> Text
        try:
            int(value, 16)
        except ValueError as e:
            for index, bit in enumerate(value, start=1):
                if bit not in string.hexdigits:
                    msg = "Could not parse value {0!r} as hex, first invalid character is {1!r} at position {2}".format(
                        value, bit, index
                    )
                    raise EnvironmentCastError(msg)
        return value

    def b64(self, value):
        # type: (Union[bytes, Text]) -> Union[bytes, Text]
        try:
            base64.urlsafe_b64decode(value)
        except (TypeError, binascii.Error) as e:
            try:
                base64.standard_b64decode(value)
            except (TypeError, binascii.Error) as e:
                raise EnvironmentCastError(
                    "Could not parse value {0!r} as URL-safe or normal base64 encoded data, {1!s}".format(
                        value, e
                    )
                )
        return value

    def importable(self, value):
        # type: (Text) -> Text
        if value[0] == "." or value[-1] == ".":
            raise EnvironmentCastError(
                "Could not parse value {0!r} as an importable, starts/ends with '.'".format(
                    value
                )
            )
        parts = value.split(".")
        for part in parts:
            # py3
            if hasattr(part, "isidentifier"):
                if part.isidentifier() is False:
                    raise EnvironmentCastError(
                        "Invalid importable path component {0!r} in {1!r}".format(
                            part, value
                        )
                    )
            # py2, slower...
            else:
                if not re.match(tokenize.Name, part):  # type: ignore
                    raise EnvironmentCastError(
                        "Invalid importable path component {0!r} in {1!r}".format(
                            part, value
                        )
                    )
        return value

    def filepath(self, value):
        # type: (Text) -> Text
        if not os.path.exists(value):
            raise EnvironmentCastError("Could not locate file {0!r}".format(value))
        if not os.path.isfile(value):
            raise EnvironmentCastError(
                "Located {0!r} but it's not a file".format(value)
            )
        try:
            fp = open(value, "r")
        except IOError as e:
            raise EnvironmentCastError(
                "Cannot open file {0!r} for reading".format(value)
            )
        else:
            fp.close()
        return value

    def directory(self, value):
        # type: (Text) -> Text
        if not os.path.exists(value):
            raise EnvironmentCastError("Could not locate directory {0!r}".format(value))
        if not os.path.isdir(value):
            raise EnvironmentCastError(
                "Located {0!r} but it's not a directory".format(value)
            )
        return value

    def web_address(self, value):
        # type: (Text) -> Text
        if (
            value[0:7] == "http://"
            or value[0:8] == "https://"
            or value[0:2] == "//"
            or (value[0] == "/" and value[1] != "/")
        ):
            return value
        raise EnvironmentCastError(
            "Could not parse {0!r} as a URL, expected it to be either absolute (http://www.example.com, https://www.example.com) or scheme-relative (//www.example.com/path), or host relative (/path)".format(
                value
            )
        )

    def decimal(self, value):
        # type: (Text) -> decimal.Decimal
        try:
            return decimal.Decimal(value)
        except decimal.InvalidOperation:
            msg = "Could not parse value {0!r} into a decimal".format(value)
            raise EnvironmentCastError(msg)

    def json(self, value):
        # type: (Text) -> Any
        return json.loads(value)


class Environment(object):
    """
    Wrapper over os.environ which provides convenience methods for fetching
    various data types (including iterables) in a hopefully sensible way.

    Also keeps track of those keys it has seen, along with the defaults/examples
    for those, so that they may be output as documentation.
    """

    __slots__ = ("source", "used", "fallbacks", "ensure")

    def __init__(self, source):
        # type: (Mapping[Text, Text]) -> None
        self.source = source
        self.used = set()  # type: Set[Tuple[Text, Text]]
        self.fallbacks = set()  # type: Set[Tuple[Text, Text]]
        self.ensure = EnvironmentCaster()

    def __repr__(self):  # type: ignore
        used = {x[0] for x in self.used}
        fallbacks = {x[0] for x in self.fallbacks}
        return "<Environment: from source={0}, from defaults={1}>".format(
            sorted(used), sorted(fallbacks)
        )

    def __str__(self):  # type: ignore
        used = {x[0] for x in self.used}
        return ", ".join(sorted(used))

    def __bool__(self):
        # type: () -> bool
        return bool(self.used)

    def __iter__(self):
        # type: () -> Iterator[Tuple[Text, Text, bool]]
        used = ((name, example, True) for name, example in self.used)
        fallbacks = ((name, example, False) for name, example in self.fallbacks)
        all_together = list(itertools.chain(used, fallbacks))
        all_together = sorted(all_together, key=operator.itemgetter(0))
        return iter(all_together)

    def __contains__(self, item):
        # type: (Text) -> bool
        return item in self.source

    def raw(self, key, default=""):
        # type: (Text, Text) -> Text
        if not isinstance(default, string_types):
            msg = "default value for {0} should be a string, so that parsing is consistent and there's a valid example value".format(
                key
            )
            raise EnvironmentDefaultError(msg)
        try:
            value = self.source[key]  # type: str
            self.used.add((key, default))
        except KeyError:
            logger.debug("Failed to read %s, using default", key)
            value = default
            self.fallbacks.add((key, default))
        return value

    def _tidy_raw_string(self, key, value):
        # type: (Text, Text) -> Text
        sq = "'"
        dq = '"'
        if len(value) <= 1:
            return value
        elif value[0] == sq and value[-1] == sq:
            logger.debug("Stripped surrounding single-quotes from %s", key)
            value = value[1:-1]
        elif value[0] == dq and value[-1] == dq:
            logger.debug("Stripped surrounding double-quotes from %s", key)
            value = value[1:-1]

        if value.lstrip() != value:
            logger.warning("Whitespace exists at beginning of %s", key)
            value = value.lstrip()
        elif value.rstrip() != value:
            logger.warning("Whitespace exists at end of %s", key)
            value = value.rstrip()

        return value

    def not_implemented(self, key, default=""):
        # type: (Text, Text) -> None
        raise NotImplementedError("Won't handle this datatype")

    def text(self, key, default=""):
        # type: (Text, Text) -> Text
        value = self.raw(key, default)
        value = self._tidy_raw_string(key, value)
        return value

    str = text
    unicode = text

    def int(self, key, default=""):
        # type: (Text, Text) -> int
        value = self.text(key, default)
        return self.ensure.int(value)

    def boolean(self, key, default=""):
        # type: (Text, Text) -> bool
        value = self.text(key, default)
        return self.ensure.boolean(value)

    bool = boolean

    def uuid(self, key, default=""):
        # type: (Text, Text) -> uuid.UUID
        value = self.text(key, default)
        return self.ensure.uuid(value)

    def datetime(self, key, default=""):
        # type: (Text, Text) -> dt.datetime
        value = self.text(key, default)
        return self.ensure.datetime(value)

    def date(self, key, default=""):
        # type: (Text, Text) -> dt.date
        value = self.text(key, default)
        return self.ensure.date(value)

    def time(self, key, default=""):
        # type: (Text, Text) -> dt.time
        value = self.text(key, default)
        return self.ensure.time(value)

    def email(self, key, default=""):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.email(value)

    def hex(self, key, default=""):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.hex(value)

    def b64(self, key, default=""):
        # type: (Text, Text) -> Union[bytes, Text]
        value = self.text(key, default)
        return self.ensure.b64(value)

    def decimal(self, key, default=""):
        # type: (Text, Text) -> decimal.Decimal
        value = self.text(key, default)
        return self.ensure.decimal(value)

    def importable(self, key, default=""):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.importable(value)

    def filepath(self, key, default=""):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.filepath(value)

    def directory(self, key, default=""):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.directory(value)

    def web_address(self, key, default=""):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.web_address(value)

    def _tidy_iterable(self, key, value, converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> Iterable[Any]
        paren_l, paren_r = "(", ")"
        sq_l, sq_r = "[", "]"
        cb_l, cb_r = "{", "}"

        if len(value) > 1:
            if value[0] == paren_l and value[-1] == paren_r:
                logger.debug("Stripped surrounding tuple identifiers from %s", key)
                value = value[1:-1]
            elif value[0] == sq_l and value[-1] == sq_r:
                logger.debug("Stripped surrounding list identifiers from %s", key)
                value = value[1:-1]
            elif value[0] == cb_l and value[-1] == cb_r:
                logger.debug(
                    "Stripped surrounding set-literal identifiers from %s", key
                )
                value = value[1:-1]

        if converter is None:

            converter = functools.partial(self._tidy_raw_string, key=key)

        else:
            if not callable(converter):
                raise EnvironmentCastError(
                    "converter=... expected to receive a callable which takes a value argument and returns a new value"
                )
        csv_spaced = ", "
        csv = ","
        if value.count(csv_spaced) == value.count(csv):
            split_by = csv_spaced
        else:
            split_by = csv
        values = (converter(value=x) for x in value.split(split_by) if x)
        return values

    def tuple(self, key, default="", converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> Tuple[Any, ...]
        return tuple(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def list(self, key, default="", converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> List[Any]
        return list(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def set(self, key, default="", converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> Set[Any]
        return set(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def frozenset(self, key, default="", converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> FrozenSet[Any]
        return frozenset(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def dict(self, key, default="", key_converter=None, value_converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]], Optional[Callable[..., Any]]) -> Dict[Any, Any]
        values = self._tidy_iterable(key, self.raw(key, default), converter=None)
        values_delimited = (v.partition("=") for v in values)

        if key_converter is None:

            if key_converter is None:
                key_converter = functools.partial(self._tidy_raw_string, key=key)

        else:
            if not callable(key_converter):
                raise EnvironmentCastError(
                    "key_converter=... expected to receive a callable which takes a value argument and returns a new value"
                )
        if value_converter is None:

            if value_converter is None:
                value_converter = functools.partial(self._tidy_raw_string, key=key)

        else:
            if not callable(value_converter):
                raise EnvironmentCastError(
                    "value_converter=... expected to receive a callable which takes a value argument and returns a new value"
                )

        keys_values = (
            (key_converter(value=key), value_converter(value=value))
            for key, delimiter, value in values_delimited
        )
        return dict(keys_values)

    def json(self, key, default=""):
        # type: (Text, Text) -> Any
        value = self.text(key, default)
        return self.ensure.json(value)

    float = not_implemented

    def one_of(self, key, default="", choices="", converter=None):
        # type: (Text, Text, Text, Optional[Callable[..., Any]]) -> Any
        if converter is None:
            converter = functools.partial(self._tidy_raw_string, key=key)
        options = tuple(sorted(self._tidy_iterable(key, choices, converter=converter)))
        value = converter(value=self.text(key, default))
        if value in options:
            return value
        raise EnvironmentCastError(
            "Could not find value {0!r} in options {1!r}".format(value, options)
        )


env = Environment(source=os.environ)


if __name__ == "__main__":
    import unittest
    import sys

    class TestBasicCasting(unittest.TestCase):
        maxDiff = 1000

        def setUp(self):
            # type: () -> None
            self.e = EnvironmentCaster()

        def test_int(self):
            # type: () -> None
            good = (("000001", 1), ("2000", 2000))
            for input, output in good:
                with self.subTest(input=input):
                    self.assertEqual(self.e.int(input), output)

        def test_int_errors(self):
            # type: () -> None
            bad = (("0x1", 0), ("aaaaa", 0), ("1000.000", 0))
            for input, output in bad:
                with self.subTest(input=input):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.int(input)

        def test_bool_trues(self):
            # type: () -> None
            good = (
                "true",
                "TRUE",
                "trUe",
                "on",
                "ON",
                "oN",
                "y",
                "Y",
                "yes",
                "YeS",
                "1",
            )
            for g in good:
                with self.subTest(input=g):
                    self.assertTrue(self.e.boolean(g))

        def test_bool_falses(self):
            # type: () -> None
            good = (
                "false",
                "FALSE",
                "fALSe",
                "off",
                "OFF",
                "Off",
                "n",
                "N",
                "no",
                "NO",
                "0",
            )
            for g in good:
                with self.subTest(input=g):
                    self.assertFalse(self.e.boolean(g))
                    self.assertFalse(self.e.bool(g))

        def test_bool_errors(self):
            # type: () -> None
            bad = ("woof", "goose", "111", "tru", "fals", "nope", "yeah")
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.boolean(i)

        def test_uuid_good(self):
            # type: () -> None
            good = (
                (
                    "1a83484205a041e2b02d02bd9fe7f382",
                    uuid.UUID("1a834842-05a0-41e2-b02d-02bd9fe7f382"),
                ),
                (
                    "1a834842-05a0-41e2-b02d-02bd9fe7f382",
                    uuid.UUID("1a834842-05a0-41e2-b02d-02bd9fe7f382"),
                ),
            )
            for input, output in good:
                with self.subTest(input=input):
                    self.assertEqual(self.e.uuid(input), output)

        def test_uuid_bad(self):
            # type: () -> None
            bad = ("test", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa")
            for i in bad:
                with self.subTest(input=input):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.uuid(i)

        @unittest.skipIf(CAN_PARSE_TEMPORAL is False, "Needs Django installed, sorry")
        def test_datetime_good(self):
            # type: () -> None
            good = (
                (
                    "2019-11-21 16:12:56.002344",
                    dt.datetime(2019, 11, 21, 16, 12, 56, 2344),
                ),
                (
                    "2019-11-21T16:12:56.002344",
                    dt.datetime(2019, 11, 21, 16, 12, 56, 2344),
                ),
                (
                    "2019-11-21 16:12:56.002344Z",
                    dt.datetime(2019, 11, 21, 16, 12, 56, 2344, tzinfo=utc),
                ),
                (
                    "2019-11-21T16:12:56.002344Z",
                    dt.datetime(2019, 11, 21, 16, 12, 56, 2344, tzinfo=utc),
                ),
                (
                    "2019-11-21 16:12:56.002344+20:00",
                    dt.datetime(
                        2019,
                        11,
                        21,
                        16,
                        12,
                        56,
                        2344,
                        tzinfo=dt.timezone(dt.timedelta(0, 72000), "+2000"),
                    ),
                ),
                (
                    "2019-11-21T16:12:56.002344+20:00",
                    dt.datetime(
                        2019,
                        11,
                        21,
                        16,
                        12,
                        56,
                        2344,
                        tzinfo=dt.timezone(dt.timedelta(0, 72000), "+2000"),
                    ),
                ),
                ("2019-11-21", dt.datetime(2019, 11, 21, 0, 0)),
            )
            for input, output in good:
                with self.subTest(input=input):
                    self.assertEqual(self.e.datetime(input), output)

        @unittest.skipIf(CAN_PARSE_TEMPORAL is False, "Needs Django installed, sorry")
        def test_datetime_bad(self):
            # type: () -> None
            bad = (
                "2019-11-21 16:50:",
                "2019-11-21 16:",
                "2019-11-21 16",
                "2019-11-21 1",
                "2019-11-21 ",
            )
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.datetime(i)

        @unittest.skipIf(CAN_PARSE_TEMPORAL is False, "Needs Django installed, sorry")
        def test_date_good(self):
            # type: () -> None
            good = (
                ("2019-11-21", dt.date(2019, 11, 21)),
                ("2019-11-2", dt.date(2019, 11, 2)),
                ("2019-03-2", dt.date(2019, 3, 2)),
                ("2019-3-2", dt.date(2019, 3, 2)),
            )
            for input, output in good:
                with self.subTest(input=input):
                    self.assertEqual(self.e.date(input), output)

        @unittest.skipIf(CAN_PARSE_TEMPORAL is False, "Needs Django installed, sorry")
        def test_date_bad(self):
            # type: () -> None
            bad = (
                "2019-13-13",
                "2019-00-00",
                "2019-0-0",
                "2019-02-",
                "2019-02",
                "2019-0",
                "2019-",
            )
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.date(i)

        @unittest.skipIf(CAN_PARSE_TEMPORAL is False, "Needs Django installed, sorry")
        def test_time_good(self):
            # type: () -> None
            good = (
                ("13:13:13.000123", dt.time(13, 13, 13, 123)),
                ("13:13:13.123", dt.time(13, 13, 13, 123000)),
                ("13:13:13", dt.time(13, 13, 13)),
                ("13:13", dt.time(13, 13)),
            )
            for input, output in good:
                with self.subTest(input=input):
                    self.assertEqual(self.e.time(input), output)

        @unittest.skipIf(CAN_PARSE_TEMPORAL is False, "Needs Django installed, sorry")
        def test_time_bad(self):
            # type: () -> None
            bad = ("13:", "13")
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.time(i)

        def test_email_bad(self):
            # type: () -> None
            bad = ("no_at_symbol", "test@test@test", "a@", "@b", "@testing", "testing@")
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.email(i)

        def test_hex_bad(self):
            # type: () -> None
            bad = ("testing", "abcdef_", "e191903936cb42be99d007941511252g")
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.hex(i)

        def test_base64_good(self):
            # type: () -> None
            good = b"d29vZg=="
            self.assertEqual(self.e.b64(good), good)

        def test_base64_bad(self):
            # type: () -> None
            bad = b"d29vZg="
            with self.assertRaises(EnvironmentCastError):
                self.e.b64(bad)

        def test_importable_bad(self):
            # type: () -> None
            bad = (".relative.import", "ends.with.", "this.looks.ok-ish.right")
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.importable(i)

        def test_filepath_good(self):
            # type: () -> None
            here = os.path.dirname(os.path.abspath(__file__))
            path = os.path.join(here, "enviable.py")
            self.assertEqual(self.e.filepath(path), path)

        def test_filepath_bad(self):
            # type: () -> None
            here = os.path.dirname(os.path.abspath(__file__))
            bad = (os.path.join(here, "non-existant", "thing_goes_here"),)
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.filepath(i)

        def test_directory_good(self):
            # type: () -> None
            here = os.path.dirname(os.path.abspath(__file__))
            self.assertEqual(self.e.directory(here), here)

        def test_directory_bad(self):
            # type: () -> None
            here = os.path.dirname(os.path.abspath(__file__))
            bad = (os.path.join(here, "non-existant", "thing_goes_here"),)
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.directory(i)

        def test_web_address_good(self):
            # type: () -> None
            good = (
                "https://example.com/path",
                "http://example.com/path",
                "//example.com/path",
                "/path",
            )
            for input in good:
                with self.subTest(input=input):
                    self.assertEqual(self.e.web_address(input), input)

        def test_web_address_bad(self):
            # type: () -> None
            bad = (
                "httpx://example.com/path",
                "http//example.com/path",
                "example.com",
                "example.com/path",
                "./whee",
            )
            for input in bad:
                with self.subTest(input=input):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.web_address(input)

        def test_decimal_bad(self):
            # type: () -> None
            bad = (
                "httpx://example.com/path",
                "http//example.com/path",
                "example.com",
                "example.com/path",
            )
            for input in bad:
                with self.subTest(input=input):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.decimal(input)

    class TestBasicEnviron(unittest.TestCase):
        def setUp(self):
            # type: () -> None
            self.e = Environment(
                {
                    "DEBUG": "on",
                    "BASE_64_ENCODED": "d29vZg==",
                    "NOT_DEBUG": "false",
                    "INTEGER": "3",
                    "UUID_HYPHENED": "ba7bbd90-b0d8-42de-9992-513268d10f45",
                    "UUID_UNHYPHENED": "ba7bbd90b0d842de9992513268d10f45",
                    "CSV_INTS": "123,4356,235",
                    "CSV_BOOLS": "true,1,off,on,yes,no,FALSE,0",
                    "IMPORTABLES": "not_a_dotted_path, a.dotted.path",
                    "DICTY": "a=1, b=2, c=4",
                }
            )

        def test_int(self):
            # type: () -> None
            self.assertEqual(self.e.int("INTEGER", "3"), 3)

        def test_b64(self):
            # type: () -> None
            self.assertEqual(self.e.b64("BASE_64_ENCODED", "3"), "d29vZg==")

        def test_bool(self):
            # type: () -> None
            self.assertFalse(self.e.boolean("NOT_DEBUG", "unused"))
            self.assertTrue(self.e.boolean("DEBUG", "unused"))

        def test_uuid(self):
            # type: () -> None
            output = uuid.UUID("ba7bbd90b0d842de9992513268d10f45")
            for x in ("UUID_HYPHENED", "UUID_UNHYPHENED"):
                with self.subTest(var=x):
                    self.assertEqual(
                        self.e.uuid(x, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"), output
                    )

        def test_tuple_of_ints(self):
            # type: () -> None
            self.assertEqual(
                self.e.tuple("CSV_INTS", ",", self.e.ensure.int), (123, 4356, 235)
            )
            self.assertEqual(self.e.tuple("CSV_INTS2", ",", self.e.ensure.int), ())

        def test_set_of_bools(self):
            # type: () -> None
            self.assertEqual(
                self.e.set("CSV_BOOLS", ",", self.e.ensure.bool), {False, True}
            )

        def test_list_of_importables(self):
            # type: () -> None
            self.assertEqual(
                self.e.list("IMPORTABLES", ",", self.e.ensure.importable),
                ["not_a_dotted_path", "a.dotted.path"],
            )

        def test_dict_of_numbers(self):
            # type: () -> None
            self.assertEqual(
                self.e.dict(
                    "DICTY",
                    ",",
                    key_converter=self.e.ensure.hex,
                    value_converter=self.e.ensure.decimal,
                ),
                {
                    "a": decimal.Decimal("1"),
                    "b": decimal.Decimal("2"),
                    "c": decimal.Decimal("4"),
                },
            )

        def test_one_of_many_choices(self):
            # type: () -> None
            self.assertEqual(
                self.e.one_of(
                    "INTEGER",
                    "100",
                    choices="123,456,3,200",
                    converter=self.e.ensure.int,
                ),
                3,
            )

        def test_one_of_many_choices_without_converter(self):
            # type: () -> None
            self.assertEqual(
                self.e.one_of("INTEGER", "100", choices="123,456,3,200",), "3",
            )

        def test_repr(self):
            # type: () -> None
            self.e.text("DEBUG", "fallback1")
            self.e.bool("DEBUG", "fallback2")
            self.e.raw("DEBUUUUG", "fallback3")
            self.assertEqual(
                repr(self.e),
                "<Environment: from source=['DEBUG'], from defaults=['DEBUUUUG']>",
            )

        def test_str(self):
            # type: () -> None
            self.e.text("DEBUG", "fallback1")
            self.e.bool("DEBUG", "fallback2")
            self.e.raw("DEBUUUUG", "fallback3")
            self.assertEqual(str(self.e), "DEBUG")

        def test_iteration(self):
            # type: () -> None
            self.e.raw("DEBUG", "fallback1")
            for x in range(3):
                self.e.raw("DEBUUUUG", "fallback2")
                self.assertEqual(
                    tuple(self.e),
                    (("DEBUG", "fallback1", True), ("DEBUUUUG", "fallback2", False)),
                )

        def test_contains(self):
            # type: () -> None
            self.assertTrue("DEBUG" in self.e)
            self.assertFalse("DEBUG1" in self.e)

    try:
        from mypy import api as mypy
    except ImportError:
        sys.stdout.write("mypy <https://mypy.readthedocs.io/> not installed\n")
        sys.stdout.write("skipped static type linting...\n\n")
    else:
        sys.stdout.write("mypy: installed, running...\n")
        old_value = os.environ.get("MYPY_FORCE_COLOR", None)
        if old_value is None:
            os.environ["MYPY_FORCE_COLOR"] = "1"
            sys.stdout.write("mypy: forcing coloured output...\n")
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "enviable.py")
        report, errors, exit_code = mypy.run(
            ["--strict", "--ignore-missing-imports", path]
        )
        if old_value is not None:
            os.environ["MYPY_FORCE_COLOR"] = old_value

        if report:
            sys.stdout.write(report)
        if errors:
            sys.stderr.write(errors)

        sys.stdout.write("mypy: run is complete...\n\n")

    unittest.main(verbosity=2)
