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
import sys
import tokenize
import uuid
import datetime as dt
from io import TextIOBase
from urllib.parse import urlparse, parse_qsl, unquote

try:
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
except ImportError:
    pass

try:
    from six import string_types
except ImportError:
    string_types = (str,)

try:
    from django.utils.dateparse import parse_date, parse_datetime, parse_time
    from django.utils.timezone import utc

    CAN_PARSE_TEMPORAL = True
    HAS_DJANGO = CAN_PARSE_TEMPORAL_DJANGO = True
except ImportError:
    HAS_DJANGO = CAN_PARSE_TEMPORAL_DJANGO = False
    # Python 3.7+ can at least parse a subset of ISO 8601 strings, like:
    # YYYY-MM-DD[*HH[:MM[:SS[.fff[fff]]]][+HH:MM[:SS[.ffffff]]]]
    # YYYY-MM-DD
    # HH[:MM[:SS[.fff[fff]]]][+HH:MM[:SS[.ffffff]]]
    try:
        dt.datetime.fromisoformat
        dt.date.fromisoformat
        dt.time.fromisoformat
    except AttributeError:

        def temporal_failure(v):  # type: ignore
            raise NotImplementedError(
                "I've not implemented parsing of dates/datetimes/times without depending on Django or Python 3.7+, sorry chum"
            )

        parse_date = temporal_failure
        parse_datetime = temporal_failure
        parse_time = temporal_failure
        utc = None
        CAN_PARSE_TEMPORAL = False
    else:

        def temporal_parser(v, handler):
            try:
                return handler(v)
            except ValueError:
                # Return None so that the rest of the date/datetime/time casting
                # can try and run. Emulate's Django's parser which returns None
                # for invalid inputs ... for reasons.
                return None

        def parse_date(v):
            return temporal_parser(v, dt.date.fromisoformat)

        def parse_datetime(v):
            return temporal_parser(v, dt.datetime.fromisoformat)

        def parse_time(v):
            return temporal_parser(v, dt.time.fromisoformat)

        utc = dt.timezone.utc
        CAN_PARSE_TEMPORAL = True


__version_info__ = "1.0.0"
__version__ = "1.0.0"
version = "1.0.0"
VERSION = "1.0.0"


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

    timedelta_re = re.compile(
        r"(?P<days>-?[0-9]{1,})\s*(?:d|dys?|days?)"
        r"|"
        r"(?P<seconds>-?[0-9]{1,})\s*(?:s|secs?|seconds?)"
        r"|"
        r"(?P<microseconds>-?[0-9]{1,})\s*(?:us?|µs?|microsecs?|microseconds?)"
        r"|"
        r"(?P<milliseconds>-?[0-9]{1,})\s*(?:ms|millisecs?|milliseconds?)"
        r"|"
        r"(?P<minutes>-?[0-9]{1,})\s*(?:m|mins?|minutes?)"
        r"|"
        r"(?P<hours>-?[0-9]{1,})\s*(?:h|hrs?|hours?)"
        r"|"
        r"(?P<weeks>-?[0-9]{1,})\s*(?:w|wks?|weeks?)",
        flags=re.UNICODE | re.IGNORECASE,
    )
    timedelta_str_re = re.compile(
        r"(?:(?P<days>-?[0-9]{1,})\s*(?:d|dys?|days?),?\s*)?"
        r"(?P<hours>[0-9]{1,})"
        r":"
        r"(?P<minutes>[0-9]{2})"
        r":"
        r"(?P<seconds>[0-9]{2})"
        r"(?:\.(?P<microseconds>[0-9]{1,6}))?",
        flags=re.UNICODE | re.IGNORECASE,
    )

    def text(self, value):
        # type: (Text) -> Text
        sq = "'"
        dq = '"'
        if len(value) <= 1:
            return value
        elif value[0] == sq and value[-1] == sq:
            value = value[1:-1]
        elif value[0] == dq and value[-1] == dq:
            value = value[1:-1]

        if value.lstrip() != value:
            value = value.lstrip()
        elif value.rstrip() != value:
            value = value.rstrip()
        return value

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

    def _just_timedelta(self, value):
        # type: (Text) -> dt.timedelta
        kwargs = {
            "days": 0,
            "seconds": 0,
            "microseconds": 0,
            "milliseconds": 0,
            "minutes": 0,
            "hours": 0,
            "weeks": 0,
        }
        timedelta_str = self.timedelta_str_re.match(value)
        if timedelta_str:
            # Parsing the output of str(timedelta(...))
            for match_kwarg, match_value in timedelta_str.groupdict().items():
                if match_value is not None:
                    kwargs[match_kwarg] = self.int(match_value)
        else:
            for match in self.timedelta_re.finditer(value):
                for match_kwarg, match_value in match.groupdict().items():
                    if match_value is not None:
                        kwargs[match_kwarg] = self.int(match_value)

        if {*kwargs.values()} == {0}:
            raise EnvironmentCastError(
                "Failed to parse any positive/negative components from timedelta string"
            )

        return dt.timedelta(**kwargs)

    def timedelta(self, value):
        # type: (Text) -> dt.timedelta
        if "=" in value:
            # should be be "1, 2, weeks=1" or "days=3, x=4"
            args, kwargs = self._guess_and_convert_string_to_arguments(value)
            # I can't use inspect.signature, I think because timedelta doesn't
            # implement __text_signature__
            try:
                return dt.timedelta(*args, **kwargs)
            except TypeError as exc:
                raise EnvironmentCastError(
                    f"Unable to convert stringified arguments representation into timedelta"
                )
        elif ";" in value or ":" in value:
            # we know for sure it's '1 week; 2 days; 3 seconds'
            # or '1 day, 6:10:12'
            return self._just_timedelta(value)
        else:
            # The format might be '1 week, 2 days, 3 minutes' or it could be
            # '1, 2, 3' as plain arguments.
            try:
                return self._just_timedelta(value)
            except EnvironmentCastError:
                # it's probably '1, 2, 3, 4'
                args, kwargs = self._guess_and_convert_string_to_arguments(value)
                # I can't use inspect.signature, I think because timedelta doesn't
                # implement __text_signature__
                try:
                    return dt.timedelta(*args, **kwargs)
                except TypeError as exc:
                    raise EnvironmentCastError(
                        f"Unable to convert stringified arguments representation into timedelta"
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
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            if len(value) > 13:
                example = "{0!s}...".format(value[0:10])
            else:
                example = value
            raise EnvironmentCastError(
                "Could not parse value {0!r} as JSON".format(example)
            )

    def _guess_and_convert_string_to_arguments(self, value):
        # type: (Text) -> Tuple[Tuple, Dict[Text, Any]]
        parts = [part.strip() for part in value.split(",") if part.strip()]
        args, kwargs = (), {}
        types = (
            self.int,
            self.datetime,
            self.date,
            self.time,
            self.decimal,
            self.uuid,
            self.bool,
            # text based fallbacks.
            self.email,
            self.hex,
            self.b64,
            self.filepath,
            self.directory,
            self.importable,
            self.web_address,
            self.json,
        )
        for part in parts:
            param_name, sep, param_value = part.partition("=")
            if not sep:
                for converter in types:
                    try:
                        converted = converter(part)
                    except EnvironmentCastError:
                        continue
                    else:
                        args += (converted,)
                        logger.debug("Converted %s using %s", part, converter)
                        break
            else:
                value = param_value.strip()
                for converter in types:
                    try:
                        converted = converter(value)
                    except EnvironmentCastError:
                        continue
                    else:
                        kwargs[param_name.strip()] = converted
                        logger.debug(
                            "Converted %s using %s",
                            param_name.strip(),
                            converter,
                        )
                        break
        return args, kwargs


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

    def print(self, format="export {key!s}='{value!s}'", stream=None):
        # type: (Text, TextIOBase) -> None
        stream = stream or sys.stdout
        try:
            from django.views.debug import SafeExceptionReporterFilter

            HIDDEN_SETTINGS = SafeExceptionReporterFilter.hidden_settings
            CLEANSED_SUBSTITUTE = SafeExceptionReporterFilter.cleansed_substitute
        except ImportError:
            try:
                from django.views.debug import HIDDEN_SETTINGS, CLEANSED_SUBSTITUTE
            except ImportError:
                HIDDEN_SETTINGS = re.compile(
                    "API|TOKEN|KEY|SECRET|PASS|SIGNATURE", flags=re.I
                )
                CLEANSED_SUBSTITUTE = "********************"
        if not format.endswith(os.linesep):
            format = format + os.linesep
        for env_var, env_example, read_from_env in self:
            if HIDDEN_SETTINGS.search(env_var):
                env_example = CLEANSED_SUBSTITUTE
            stream.write(format.format(key=env_var, value=env_example))

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
        tidied = self.ensure.text(value)
        if tidied != value:
            logger.debug("Stripped surrounding quotes from %s", key)
        return tidied

    def not_implemented(self, key, default=""):
        # type: (Text, Text) -> None
        raise NotImplementedError("Won't handle this datatype")

    def text(self, key, default):
        # type: (Text, Text) -> Text
        value = self.raw(key, default)
        value = self._tidy_raw_string(key, value)
        return value

    str = text
    unicode = text

    def int(self, key, default):
        # type: (Text, Text) -> int
        value = self.text(key, default)
        return self.ensure.int(value)

    def boolean(self, key, default):
        # type: (Text, Text) -> bool
        value = self.text(key, default)
        return self.ensure.boolean(value)

    bool = boolean

    def uuid(self, key, default):
        # type: (Text, Text) -> uuid.UUID
        value = self.text(key, default)
        return self.ensure.uuid(value)

    def datetime(self, key, default):
        # type: (Text, Text) -> dt.datetime
        value = self.text(key, default)
        return self.ensure.datetime(value)

    def date(self, key, default):
        # type: (Text, Text) -> dt.date
        value = self.text(key, default)
        return self.ensure.date(value)

    def time(self, key, default):
        # type: (Text, Text) -> dt.time
        value = self.text(key, default)
        return self.ensure.time(value)

    def timedelta(self, key, default):
        # type: (Text, Text) -> dt.timedelta
        value = self.text(key, default)
        return self.ensure.timedelta(value)

    def email(self, key, default):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.email(value)

    def hex(self, key, default):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.hex(value)

    def b64(self, key, default):
        # type: (Text, Text) -> Union[bytes, Text]
        value = self.text(key, default)
        return self.ensure.b64(value)

    def decimal(self, key, default):
        # type: (Text, Text) -> decimal.Decimal
        value = self.text(key, default)
        return self.ensure.decimal(value)

    def importable(self, key, default):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.importable(value)

    def filepath(self, key, default):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.filepath(value)

    def directory(self, key, default):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.directory(value)

    def web_address(self, key, default):
        # type: (Text, Text) -> Text
        value = self.text(key, default)
        return self.ensure.web_address(value)

    def django_database_url(self, key="", default=""):
        # type: (Text, Text) -> Dict[Text, Union[boolean, int, Text, Dict[Text, Text]]]
        # Facilitate swapping, so that you can do the following:
        # env.django_database_url("sqlite://:memory:") and have it
        # successfully read the value from the database.
        # or alternatively do:
        # env.django_database_url("MY_DB")
        # which won't give you a default value ...
        if default == "":
            if key == "":
                key = "DATABASE_URL"
            elif "://" in key:
                default = key
                key = "DATABASE_URL"
        elif key == "":
            key = "DATABASE_URL"

        if not default:
            raise TypeError("{cls}.django_database_url() missing 1 required argument: 'default'".format(cls=self.__class__.__name__))

        aliases = {
            "postgre": "postgres",
            "postgregis": "postgis",
            "postgresql": "postgres",
            "postgresqlgis": "postgis",
            "psycopg2": "postgres",
            "psycopg2gis": "postgis",
            "psql": "postgres",
            "psqlgis": "postgis",
            "pgsql": "postgres",
            "pgsqlgis": "postgis",
            "pg": "postgres",
            "pggis": "postgis",
            "pgis": "postgis",
            "mariadb": "mysql",
            "maria": "mysql",
            "mariadbgis": "mysqlgis",
            "mariagis": "mysqlgis",
            "mysqlclient": "mysql",
            "mysqlclientgis": "mysqlgis",
            "sqlite3": "sqlite",
            "mysql-connector": "mysqlconnector",
            "mysql-connecter": "mysqlconnector",
            "mysql_connector": "mysqlconnector",
            "mysql_connecter": "mysqlconnector",
            "awsredshift": "redshift",
            "aws_redshift": "redshift",
            "aws-redshift": "redshift",
            "ldapdb": "ldap",
        }

        builtin_scheme_map = {
            "postgres": "django.db.backends.postgresql",
            "mysql": "django.db.backends.mysql",
            "sqlite": "django.db.backends.sqlite3",
            "mysqlconnector": "mysql.connector.django",
            "redshift": "django_redshift_backend",
            "oracle": "django.db.backends.oracle",
            "mssql": "mssql",  # https://github.com/microsoft/mssql-django
            "ldap": "ldapdb.backends.ldap",  # https://github.com/django-ldapdb/django-ldapdb
            "mysqlgis": "django.contrib.gis.db.backends.mysql",
            "postgis": "django.contrib.gis.db.backends.postgis",
        }

        def int_or_none(item_to_convert):
            try:
                return self.ensure.int(item_to_convert)
            except EnvironmentCastError as exc:
                if item_to_convert.strip().lower() == "none":
                    return None
                raise EnvironmentCastError(str(exc))

        global_options = {
            "ATOMIC_REQUESTS": self.ensure.boolean,
            "AUTOCOMMIT": self.ensure.boolean,
            "CONN_MAX_AGE": int_or_none,
            "TIME_ZONE": self.ensure.text,
            "DISABLE_SERVER_SIDE_CURSORS": self.ensure.boolean,
            "CHARSET": self.ensure.text,
            "COLLATION": self.ensure.text,
            "MIGRATE": self.ensure.boolean,
            "TEMPLATE": self.ensure.text,
        }
        value = self.text(key, default)
        result = urlparse(value)
        # scheme, netloc, path, params, query, fragment = result
        if result.scheme in builtin_scheme_map:
            engine = builtin_scheme_map[result.scheme]
        elif result.scheme in aliases:
            result_scheme = aliases[result.scheme]
            engine = builtin_scheme_map[result_scheme]
        else:
            # dotted path to selected engine.
            engine = env.ensure.importable(result.scheme)

        path = result.path[1:]
        host, port = result._hostinfo
        if engine == "django.db.backends.sqlite3":
            # It got parsed as the port part, so it's missing the colon prefix
            if port == "memory:":
                if result.path and result.path != "/":
                    raise EnvironmentCastError(
                        f"Unexpected path ({result.path}) for an in-memory SQLite database"
                    )
                path = ":memory:"
                port = None
                host = None
            # empty = memory, for compatibility with django-environ & dj-database-url
            # which apparently do it for sqlalchemy:
            # https://github.com/joke2k/django-environ/blob/44ac6649ad6135ff4246371880298bf732cd1c52/environ/environ.py#L487-L492
            # https://github.com/jacobian/dj-database-url/blob/1937ed9e61d273163353c8546825dd529ce8546c/dj_database_url.py#L98-L101
            elif path == "":
                path = ":memory:"
                port = None
                host = None
        # Complex case for postgres family stuff, for compatibility with
        # django-environ
        # https://github.com/joke2k/django-environ/blob/44ac6649ad6135ff4246371880298bf732cd1c52/environ/environ.py#L513-L517
        elif path and path[0] == "/":
            if "cloudsql" in path or engine in {
                "django.db.backends.postgresql",
                "django.contrib.gis.db.backends.postgis",
            }:
                host, path = path.rsplit("/", 1)
        # Special-case for oracle.
        # https://github.com/joke2k/django-environ/blob/44ac6649ad6135ff4246371880298bf732cd1c52/environ/environ.py#L519-L521
        elif engine == "django.db.backends.oracle" and not path:
            path = host
            host = None
        # Special-case LDAP, fo compatibility with django-environ
        # https://github.com/joke2k/django-environ/blob/44ac6649ad6135ff4246371880298bf732cd1c52/environ/environ.py#L496-L502
        elif engine == "ldapdb.backends.ldap":
            path = "{}://{}".format(result.scheme, host)
            if port:
                path += ":{}".format(port)

        parsed_config = {
            "ENGINE": engine,
            "NAME": unquote(path),
            "OPTIONS": {},
        }
        if result.username:
            parsed_config["USER"] = unquote(self.ensure.text(result.username))
        if result.password:
            parsed_config["PASSWORD"] = unquote(self.ensure.text(result.password))
        if host:
            parsed_config["HOST"] = unquote(self.ensure.text(host))
        if port:
            parsed_config["PORT"] = env.ensure.int(unquote(self.ensure.text(port)))

        # query string is both global options and OPTIONS, for compatibility
        # with things like django-environ
        if result.query:
            for key, value in parse_qsl(result.query):
                capkey = key.upper()
                value = unquote(value)
                if capkey in global_options:
                    converter = global_options[capkey]
                    parsed_config.update({capkey: converter(value)})
                else:
                    parsed_config["OPTIONS"].update({key: self.ensure.text(value)})

        # if given a fragment, those are all global options.
        if result.fragment:
            for key, value in parse_qsl(result.fragment):
                capkey = key.upper()
                if capkey in parsed_config:
                    raise EnvironmentCastError(
                        f"fragment key ({key}) conflicts with previously set query-string key ({capkey})"
                    )
                value = unquote(value)
                if capkey in global_options:
                    converter = global_options[capkey]
                    parsed_config.update({capkey: converter(value)})
                else:
                    parsed_config.update({key: self.ensure.text(value)})

        # Special case, setting the ssl-ca into a nested dictionary
        # for compatibility with dj-database-url
        # https://github.com/jacobian/dj-database-url/blob/1937ed9e61d273163353c8546825dd529ce8546c/dj_database_url.py#L133-L135
        if "ssl-ca" in parsed_config["OPTIONS"] and engine in {
            "django.db.backends.mysql",
            "django.contrib.gis.db.backends.mysql",
        }:
            keypath = parsed_config["OPTIONS"].pop("ssl-ca")
            parsed_config["OPTIONS"].setdefault("ssl", {})
            parsed_config["OPTIONS"]["ssl"]["ca"] = self.ensure.text(keypath)

        # Special-case, append the search path if currentSchema is given for a
        # postgres based engine. For compatibility with dj-database-url
        # https://github.com/jacobian/dj-database-url/blob/1937ed9e61d273163353c8546825dd529ce8546c/dj_database_url.py#L142-L149
        # https://stackoverflow.com/questions/51360469/django-postgresql-set-statement-timeout
        # https://www.postgresql.org/docs/9.6/libpq-connect.html#LIBPQ-PARAMKEYWORDS
        # https://www.postgresql.org/docs/9.2/config-setting.html#CONFIG-SETTING-OTHER-METHODS
        if "currentSchema" in parsed_config["OPTIONS"] and engine in {
            "django.db.backends.postgresql",
            "django.contrib.gis.db.backends.postgis",
            "django_redshift_backend",
        }:
            schema = parsed_config["OPTIONS"].pop("currentSchema")
            parsed_config["OPTIONS"].setdefault("options", "")
            parsed_config["OPTIONS"]["options"] += " -c search_path={!s}".format(schema)
            parsed_config["OPTIONS"]["options"] = parsed_config["OPTIONS"][
                "options"
            ].strip()

        return parsed_config

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

    def tuple(self, key, default, converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> Tuple[Any, ...]
        return tuple(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def list(self, key, default, converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> List[Any]
        return list(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def set(self, key, default="", converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> Set[Any]
        return set(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def frozenset(self, key, default, converter=None):
        # type: (Text, Text, Optional[Callable[..., Any]]) -> FrozenSet[Any]
        return frozenset(
            self._tidy_iterable(key, self.raw(key, default), converter=converter)
        )

    def dict(self, key, default, key_converter=None, value_converter=None):
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

    def json(self, key, default):
        # type: (Text, Text) -> Any
        value = self.text(key, default)
        return self.ensure.json(value)

    float = not_implemented

    def one_of(self, key, default, choices="", converter=None):
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

        @unittest.skipIf(
            CAN_PARSE_TEMPORAL is False, "Needs Django or Python 3.7+ installed, sorry"
        )
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

        @unittest.skipIf(
            CAN_PARSE_TEMPORAL_DJANGO is False, "Needs Django installed, sorry"
        )
        def test_datetime_good_django_specifics(self):
            good = (
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
                "2019-11-21 1",
                "2019-11-21 ",
            )
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.datetime(i)

        @unittest.skipIf(
            CAN_PARSE_TEMPORAL_DJANGO is False, "Needs Django installed, sorry"
        )
        def test_datetime_bad_django_specifics(self):
            # type: () -> None
            bad = (
                # I think this is irrelevant after django/django@f35ab74752adb37138112657c1bc8b91f50e799b
                # "2019-11-21 16",
            )
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.datetime(i)

        @unittest.skipIf(
            CAN_PARSE_TEMPORAL is False, "Needs Django or Python 3.7+ installed, sorry"
        )
        def test_date_good(self):
            # type: () -> None
            self.assertEqual(self.e.date("2019-11-21"), dt.date(2019, 11, 21))

        @unittest.skipIf(
            CAN_PARSE_TEMPORAL_DJANGO is False, "Needs Django installed, sorry"
        )
        def test_date_good_django_specifics(self):
            # type: () -> None
            good = (
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

        @unittest.skipIf(
            CAN_PARSE_TEMPORAL is False, "Needs Django or Python 3.7+ installed, sorry"
        )
        def test_time_bad(self):
            # type: () -> None
            bad = ("13:",)
            for i in bad:
                with self.subTest(input=i):
                    with self.assertRaises(EnvironmentCastError):
                        self.e.time(i)

        @unittest.skipIf(CAN_PARSE_TEMPORAL is False, "Needs Django installed, sorry")
        def test_time_bad_django_specifics(self):
            # type: () -> None
            bad = ("13:",)
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

        def test_timedeltas_good(self):
            good = (
                ("1 day", dt.timedelta(days=1)),
                ("1 day, 10 minute", dt.timedelta(days=1, minutes=10)),
                ("1 day; 10 minutes", dt.timedelta(days=1, minutes=10)),
                (
                    "1 day, 10 minutes, 4 seconds",
                    dt.timedelta(days=1, minutes=10, seconds=4),
                ),
                (
                    "1 day; 10 minutes, 4 seconds",
                    dt.timedelta(days=1, minutes=10, seconds=4),
                ),
                (
                    "1 day, 10 minutes, 4 second, 10 milliseconds",
                    dt.timedelta(days=1, minutes=10, seconds=4, milliseconds=10),
                ),
                (
                    "1 day, 10 minutes; 4 seconds; 10 millisecond",
                    dt.timedelta(days=1, minutes=10, seconds=4, milliseconds=10),
                ),
                ("weeks=1, days=2", dt.timedelta(weeks=1, days=2)),
                ("1,2, 3, 4", dt.timedelta(1, 2, 3, 4)),
                ("1 minutes, 3secs", dt.timedelta(minutes=1, seconds=3)),
                ("1 minutes, 3sec", dt.timedelta(minutes=1, seconds=3)),
                ("1 minutes, 3s", dt.timedelta(minutes=1, seconds=3)),
                (
                    "10wks, 4min, 10s, 9ms, 4us",
                    dt.timedelta(
                        weeks=10, minutes=4, seconds=10, milliseconds=9, microseconds=4
                    ),
                ),
                ("5hr,34m,56s", dt.timedelta(hours=5, minutes=34, seconds=56)),
                ("5hr34m56s", dt.timedelta(hours=5, minutes=34, seconds=56)),
                (
                    "5 days and 31 minutes and 10 seconds",
                    dt.timedelta(days=5, minutes=31, seconds=10),
                ),
                (
                    "50 days, 6:05:02.004003",
                    dt.timedelta(days=50, seconds=21902, microseconds=4003),
                ),
                ("3d", dt.timedelta(days=3)),
                ("-37D", dt.timedelta(days=-37)),
                ("129m", dt.timedelta(minutes=129)),
                ("-3d-5h", dt.timedelta(days=-3, hours=-5)),
                ("-13d19m", dt.timedelta(days=-13, minutes=19)),
                ("-4 days, 0:00:00", dt.timedelta(days=-4)),
                ("-1 day, 23:59:59.999000", dt.timedelta(milliseconds=-1)),
                ("0:00:00.001000", dt.timedelta(milliseconds=1)),
                ("0:00:00.000004", dt.timedelta(microseconds=4)),
                ("0:00:01", dt.timedelta(seconds=1)),
                ("0:01:00", dt.timedelta(minutes=1)),
                ("50 days, 6:05:02.004003", dt.timedelta(1, 2, 3, 4, 5, 6, 7)),
                ("700000000 days, 0:00:00", dt.timedelta(weeks=100000000)),
            )
            for input, output in good:
                with self.subTest(input=input):
                    self.assertEqual(self.e.timedelta(input), output)

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
                self.e.one_of(
                    "INTEGER",
                    "100",
                    choices="123,456,3,200",
                ),
                "3",
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

        def test_database_url(self):
            examples = (
                (
                    "sqlite:////tmp/my-tmp-sqlite.db",
                    {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": "/tmp/my-tmp-sqlite.db",
                        "OPTIONS": {},
                    },
                ),
                (
                    "sqlite://:memory:",
                    {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": ":memory:",
                        "OPTIONS": {},
                    },
                ),
                (
                    "pg://user:p%40ss@localhost:9991/dbname?atomic_requests=1#AUTOCOMMIT=1",
                    {
                        "ATOMIC_REQUESTS": True,
                        "AUTOCOMMIT": True,
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "localhost",
                        "PORT": 9991,
                        "NAME": "dbname",
                        "OPTIONS": {},
                        "PASSWORD": "p@ss",
                        "USER": "user",
                    },
                ),
                (
                    "maria://%F0%9F%98%80:%E3%81%81@localhost:22/test_db?init_command=SET%20sql_mode%3D%27STRICT_ALL_TABLES%27&read_default_file=%2Fpath%2Fto%2Ffile.cnf#test=lol",
                    {
                        "ENGINE": "django.db.backends.mysql",
                        "HOST": "localhost",
                        "PORT": 22,
                        "NAME": "test_db",
                        "OPTIONS": {
                            "init_command": "SET sql_mode='STRICT_ALL_TABLES'",
                            "read_default_file": "/path/to/file.cnf",
                        },
                        "PASSWORD": "ぁ",
                        "USER": "😀",
                        "test": "lol",
                    },
                ),
            )
            for url, output in examples:
                with self.subTest(url=url):
                    env = Environment({"DATABASE_URL": url})
                    self.assertDictEqual(output, env.django_database_url(default="sqlite:////should/never/be/used.db"))

        def test_database_url_switching(self):
            env = Environment(
                {
                    "DATABASE_URL": "sqlite://:memory:",
                    "OTHER_DATABASE": "sqlite:////path/to/file.db",
                }
            )
            with self.subTest("blank key, blank default"):
                with self.assertRaises(TypeError):
                    env.django_database_url()
                with self.assertRaises(TypeError):
                    env.django_database_url(key="", default="")
            with self.subTest("key only (no default)"):
                with self.assertRaises(TypeError):
                    env.django_database_url("OTHER_DATABASE")
                with self.assertRaises(TypeError):
                    env.django_database_url(key="OTHER_DATABASE")

            with self.subTest("only default (no key)"):
                # falls back to using DATABASE_URL, rather than OTHER_DB or the fallback
                # because the key is implicitly turned into `DATABASE_URL`
                out = {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": ":memory:",
                    "OPTIONS": {},
                }
                self.assertDictEqual(
                    out, env.django_database_url("sqlite:////test/file.db")
                )
                self.assertDictEqual(
                    out, env.django_database_url(default="sqlite:////test/file.db")
                )

            with self.subTest("key & default"):
                # THIRD_DB doesn't exist, so use the fallback
                out = {
                    "ENGINE": "django.db.backends.sqlite3",
                    "NAME": "/test/file.db",
                    "OPTIONS": {},
                }
                self.assertDictEqual(
                    out,
                    env.django_database_url("THIRD_DB", "sqlite:////test/file.db"),
                )
                self.assertDictEqual(
                    out,
                    env.django_database_url(
                        key="THIRD_DB", default="sqlite:////test/file.db"
                    ),
                )

        def test_database_url_via_django_environ_examples(self):
            """
            examples taken from https://github.com/joke2k/django-environ/blob/main/tests/test_db.py
            """
            django_environ_examples = (
                (
                    "postgres://user:password@//cloudsql/project-1234:us-central1:instance/dbname",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "PASSWORD": "password",
                        "USER": "user",
                        "NAME": "dbname",
                        "HOST": "/cloudsql/project-1234:us-central1:instance",
                        "OPTIONS": {},
                    },
                ),
                (
                    "sqlite://missing-slash-path",
                    {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": ":memory:",
                        "OPTIONS": {},
                    },
                ),
                (
                    "'postgres://user:pass@host:1234/dbname?conn_max_age=600'",
                    {
                        "CONN_MAX_AGE": 600,
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "host",
                        "NAME": "dbname",
                        "OPTIONS": {},
                        "PASSWORD": "pass",
                        "PORT": 1234,
                        "USER": "user",
                    },
                ),
                (
                    "postgres://user:pass@host:1234/dbname?"
                    "conn_max_age=None&autocommit=True&atomic_requests=False",
                    {
                        "ATOMIC_REQUESTS": False,
                        "AUTOCOMMIT": True,
                        "CONN_MAX_AGE": None,
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "host",
                        "NAME": "dbname",
                        "OPTIONS": {},
                        "PASSWORD": "pass",
                        "PORT": 1234,
                        "USER": "user",
                    },
                ),
                (
                    "mysql://user:pass@host:1234/dbname?init_command=SET storage_engine=INNODB",
                    {
                        "ENGINE": "django.db.backends.mysql",
                        "HOST": "host",
                        "NAME": "dbname",
                        "OPTIONS": {"init_command": "SET storage_engine=INNODB"},
                        "PASSWORD": "pass",
                        "PORT": 1234,
                        "USER": "user",
                    },
                ),
                (
                    "postgres://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "postgres:////var/run/postgresql/db",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "NAME": "db",
                        "HOST": "/var/run/postgresql",
                        "OPTIONS": {},
                    },
                ),
                (
                    "postgis://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn",
                    {
                        "ENGINE": "django.contrib.gis.db.backends.postgis",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "mysqlgis://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn",
                    {
                        "ENGINE": "django.contrib.gis.db.backends.mysql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "mysql://bea6eb025ca0d8:69772142@us-cdbr-east.cleardb.com/heroku_97681db3eff7580?reconnect=true",
                    {
                        "ENGINE": "django.db.backends.mysql",
                        "HOST": "us-cdbr-east.cleardb.com",
                        "NAME": "heroku_97681db3eff7580",
                        "OPTIONS": {"reconnect": "true"},
                        "PASSWORD": "69772142",
                        "USER": "bea6eb025ca0d8",
                    },
                ),
                (
                    "mysql://travis@localhost/test_db",
                    {
                        "ENGINE": "django.db.backends.mysql",
                        "HOST": "localhost",
                        "NAME": "test_db",
                        "OPTIONS": {},
                        "USER": "travis",
                    },
                ),
                (
                    "sqlite://",
                    {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": ":memory:",
                        "OPTIONS": {},
                    },
                ),
                (
                    "sqlite:////full/path/to/your/file.sqlite",
                    {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": "/full/path/to/your/file.sqlite",
                        "OPTIONS": {},
                    },
                ),
                (
                    "sqlite://:memory:",
                    {
                        "ENGINE": "django.db.backends.sqlite3",
                        "NAME": ":memory:",
                        "OPTIONS": {},
                    },
                ),
                (
                    "ldap://cn=admin,dc=nodomain,dc=org:some_secret_password@ldap.nodomain.org/",
                    {
                        "ENGINE": "ldapdb.backends.ldap",
                        "HOST": "ldap.nodomain.org",
                        "NAME": "ldap://ldap.nodomain.org",
                        "OPTIONS": {},
                        "PASSWORD": "some_secret_password",
                        "USER": "cn=admin,dc=nodomain,dc=org",
                    },
                ),
            )
            for url, output in django_environ_examples:
                with self.subTest(url=url):
                    env = Environment({"DATABASE_URL": url})
                    self.assertDictEqual(output, env.django_database_url(default="sqlite:////should/never/be/used.db"))

        def test_database_url_from_dj_database_url(self):
            """
            examples taken from https://github.com/jacobian/dj-database-url/blob/master/test_dj_database_url.py
            """
            dj_database_url_examples = (
                (
                    "postgres://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "postgres://%2Fvar%2Frun%2Fpostgresql/d8r82722r2kuvn",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "/var/run/postgresql",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                    },
                ),
                (
                    "postgres://%2FUsers%2Fpostgres%2FRuN/d8r82722r2kuvn",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "/Users/postgres/RuN",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                    },
                ),
                (
                    "postgres://ieRaekei9wilaim7:wegauwhgeuioweg@[2001:db8:1234::1234:5678:90af]:5431/d8r82722r2kuvn",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "2001:db8:1234::1234:5678:90af",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "ieRaekei9wilaim7",
                    },
                ),
                (
                    "postgres://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn?currentSchema=otherschema",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {"options": "-c search_path=otherschema"},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "postgres://%23user:%23password@ec2-107-21-253-135.compute-1.amazonaws.com:5431/%23database",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "#database",
                        "OPTIONS": {},
                        "PASSWORD": "#password",
                        "PORT": 5431,
                        "USER": "#user",
                    },
                ),
                (
                    "mysql-connector://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn",
                    {
                        "ENGINE": "mysql.connector.django",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                # (
                #     "django_mysqlpool.backends.mysqlpool://bea6eb025ca0d8:69772142@us-cdbr-east.cleardb.com/heroku_97681db3eff7580?reconnect=true",
                #     {},
                # ),
                (
                    "postgres://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn?sslrootcert=rds-combined-ca-bundle.pem&sslmode=verify-full",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {
                            "sslmode": "verify-full",
                            "sslrootcert": "rds-combined-ca-bundle.pem",
                        },
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "postgres://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5431/d8r82722r2kuvn?",
                    {
                        "ENGINE": "django.db.backends.postgresql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5431,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "mysql://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:3306/d8r82722r2kuvn?ssl-ca=rds-combined-ca-bundle.pem",
                    {
                        "ENGINE": "django.db.backends.mysql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {"ssl": {"ca": "rds-combined-ca-bundle.pem"}},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 3306,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "oracle://scott:tiger@oraclehost:1521/hr",
                    {
                        "ENGINE": "django.db.backends.oracle",
                        "HOST": "oraclehost",
                        "NAME": "hr",
                        "OPTIONS": {},
                        "PASSWORD": "tiger",
                        "PORT": 1521,
                        "USER": "scott",
                    },
                ),
                (
                    "oracle://scott:tiger@/(DESCRIPTION=(ADDRESS_LIST=(ADDRESS=(PROTOCOL=TCP)(HOST=oraclehost)(PORT=1521)))(CONNECT_DATA=(SID=hr)))",
                    {
                        "ENGINE": "django.db.backends.oracle",
                        "NAME": "(DESCRIPTION=(ADDRESS_LIST=(ADDRESS=(PROTOCOL=TCP)(HOST=oraclehost)(PORT=1521)))(CONNECT_DATA=(SID=hr)))",
                        "OPTIONS": {},
                        "PASSWORD": "tiger",
                        "USER": "scott",
                    },
                ),
                (
                    "oracle://scott:tiger@/tnsname",
                    {
                        "ENGINE": "django.db.backends.oracle",
                        "NAME": "tnsname",
                        "OPTIONS": {},
                        "PASSWORD": "tiger",
                        "USER": "scott",
                    },
                ),
                (
                    "redshift://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com:5439/d8r82722r2kuvn?currentSchema=otherschema",
                    {
                        "ENGINE": "django_redshift_backend",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {"options": "-c search_path=otherschema"},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 5439,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "mssql://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com/d8r82722r2kuvn?driver=ODBC Driver 13 for SQL Server",
                    {
                        "ENGINE": "mssql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {"driver": "ODBC Driver 13 for SQL Server"},
                        "PASSWORD": "wegauwhgeuioweg",
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
                (
                    "mssql://uf07k1i6d8ia0v:wegauwhgeuioweg@ec2-107-21-253-135.compute-1.amazonaws.com\\insnsnss:12345/d8r82722r2kuvn?driver=ODBC Driver 13 for SQL Server",
                    {
                        "ENGINE": "mssql",
                        "HOST": "ec2-107-21-253-135.compute-1.amazonaws.com\\insnsnss",
                        "NAME": "d8r82722r2kuvn",
                        "OPTIONS": {"driver": "ODBC Driver 13 for SQL Server"},
                        "PASSWORD": "wegauwhgeuioweg",
                        "PORT": 12345,
                        "USER": "uf07k1i6d8ia0v",
                    },
                ),
            )
            for url, output in dj_database_url_examples:
                with self.subTest(url=url):
                    env = Environment({"DATABASE_URL": url})
                    self.assertDictEqual(output, env.django_database_url(default="sqlite:////should/never/be/used.db"))

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
