enviable
========

:author: Keryn Knight
:version: 1.0.0

A small module for wrapping over environment variables (pulled from ``os.environ``)
which provides convenience methods to fetch and check various data types
(including iterables) in what I'd charitably hope is a semi-sensible way.


Rationale
---------

I've worked on enough projects of long lifespan that ultimately everyone ends up with
their own environment variables configured, invariably differently and often out-of-date
or stale in unique ways, depending on when they arrive and the maturity and state of
the project at the time.

This is an attempt to resolve that by having a single configuration, read from env vars,
and capable of dumping an env file full of valid example values which should be enough
to run safely locally.

Explicitly *doesn't* attempt to read from any ``.env`` or ``.envrc`` file, because that
doesn't describe valid examples or *which* things may/should be set into the
environment. It becomes an absolute pot-luck. It also means you end up carrying around
stale environment variables because some change or other wasn't mentioned/documented
and so env var ``MY_COOL_ENV`` isn't used any more.

Tracks requested environment variables and their default/fallback/example values, and
whether or not the fallback was used. Never tracks the actual environment value.

If this package isn't to your liking, there's **plenty** of others, and I'm
largely suffering from *Not-Invented-Here syndrome*.

All methods exposed by the Environment accept a key and a default.

- The key is the environment variable to search for.
- The default **MUST** be a string, as it is subject to the same parsing as if it had
  been found in the environment, and thus serves as a documented example of a valid
  value to export as an environment variable. Enforced value documentation!

A series of examples
--------------------

A short overview of all of the available check/cast methods on an ``Environment`` follows

Assume all examples are prefixed with::

    from enviable import env

which is roughly equivalent to::

    from enviable import Environment
    import os
    env = Environment(os.environ)

Remember, the **second** argument (``default``) is always a string, and always
gets parsed the same as a real value, so treat it as an example value in the following...

Conversions and validations
^^^^^^^^^^^^^^^^^^^^^^^^^^^

To trim any accidental quotes or whitespace from the beginning and end of the value::

    env.text("VAR_NAME", "'   test '") == "test"

To convert an incoming string to an integer::

    env.int("VAR_NAME", "3") == 3

To convert an incoming string to real boolean (``True`` or ``False``), note
that upper or lower case doesn't matter::

    env.bool("VAR_NAME", "true") is True
    env.bool("VAR_NAME", "on") is True
    env.bool("VAR_NAME", "1") is True
    env.bool("VAR_NAME", "yes") is True
    env.bool("VAR_NAME", "y") is True

    env.bool("VAR_NAME", "false") is False
    env.bool("VAR_NAME", "off") is False
    env.bool("VAR_NAME", "0") is False
    env.bool("VAR_NAME", "no") is False
    env.bool("VAR_NAME", "n") is False

To make a ``uuid.UUID`` from an optionally hyphenated string::

    env.uuid("VAR_NAME", "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa") == UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
    env.uuid("VAR_NAME", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") == UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')

To *check* if an email *looks* valid::

    env.email("VAR_NAME", "a@b.com") == "a@b.com"

To verify if a string is all hexi characters::

    env.hex("VAR_NAME", "abcdef12345ABCDEF") == "abcdef12345ABCDEF"

Confirm that a string can be decoded from a base64 encoded value::

    env.b64("VAR_NAME", "d29vZg==") == 'd29vZg=='

There's no support for ``float`` because it's lossy, but you can have decimals::

    env.decimal("VAR_NAME", "1.25") == Decimal("1.25")

To confirm that a string looks like it might be an importable python thing::

    env.importable("VAR_NAME", "path.to.my.module") == "path.to.my.module"

To make sure a string represents an existing, *readable* file on disk::

    env.filepath("VAR_NAME", "/path/to/my/valid_file.json") == "/path/to/my/valid_file.json"

To make sure a string is a directory which exists::

    env.directory("VAR_NAME", "/path/to/my") == "/path/to/my"

To *vaguely* sanity-check URLs (must start with ``http://`` or ``https://`` or ``//`` or ``/...``)::

    env.web_address("VAR_NAME", "http://example.com/")

To constrain a value to one of a few valid options (where ``choices`` is parsed the same way as `Iterables`_)::

    env.one_of("VAR_NAME", "3", choices="1,2,3,4")

and to go off-reservation, you can get JSON out, or the raw environment string::

    env.json("VAR_NAME", "{}") == {}
    env.raw("VAR_NAME", "'   ...  '") == "'   ...  '"

Temporal values (datetimes, dates, times, timedeltas)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

If you have `Django`_ (or Python **3.7+**) installed (because that's my main use case and I'm lazy)
you can also get datetimes if you provide a value in ISO 8601 format::

    env.datetime("VAR_NAME", "2019-11-21 16:12:56.002344")
    env.datetime("VAR_NAME", "2019-11-21 16:12:56.002344+20:00")
    env.datetime("VAR_NAME", "2019-11-21")

Similarly you can ask for dates::

    env.date("VAR_NAME", "2019-11-21")
    env.date("VAR_NAME", "2019-11-2")
    env.date("VAR_NAME", "2019-3-2")

or times::

    env.time("VAR_NAME", "13:13:13.000123")
    env.time("VAR_NAME", "13:13:13.123")
    env.time("VAR_NAME", "13:13:13")
    env.time("VAR_NAME", "13:13")

or timedeltas (which **do not** depend on `Django`_)::

    env.timedelta("VAR_NAME", "1 day, 10 minutes")
    env.timedelta("VAR_NAME", "1 day, 10 minutes; 4 seconds; 10 millisecond")
    env.timedelta("VAR_NAME", "1 minutes, 3secs")
    env.timedelta("VAR_NAME", "10wks, 4min, 10s, 9ms, 4us")
    env.timedelta("VAR_NAME", "-13d19m")
    env.timedelta("VAR_NAME", "-1 day, 23:59:59.999000")
    env.timedelta("VAR_NAME", "0:00:00.001000")

Iterables
^^^^^^^^^

It's additionally possible to consume a string and cast it to a sequence etc::

    env.tuple("VAR_NAME", "123,4356,235") == ("123", "4356", "235")
    env.list("VAR_NAME", "123,4356,235") == ["123", "4356", "235"]
    env.set("VAR_NAME", "123,4356,235") == {"123", "4356", "235"}
    env.frozenset("VAR_NAME", "123,4356,235") == {"123", "4356", "235"}
    env.dict("VAR_NAME", "a=1, b=2, c=3") == {"a": "1", "b": "2", "c": "3"}

Commas are treated as delimiters, and may optionally have a single space after each one.

Leading python-iterable characters are dropped if they are present from both sides,
and their python type is ignored::

    env.tuple("VAR_NAME", "[123, 4356, 235]") == ("123", "4356", "235")
    env.tuple("VAR_NAME", "(123, 4356, 235)") == ("123", "4356", "235")
    env.tuple("VAR_NAME", "{123, 4356, 235}") == ("123", "4356", "235")

Casting on iterables
^^^^^^^^^^^^^^^^^^^^

Using any of ``env.tuple``, ``env.list``, ``env.set``, ``env.frozenset``,
or ``env.dict`` allows each parsed value to be validated and optionally cast,
with the caveat that the *iterable is homogenous* (that is, everything can be
converted to an ``int`` or a ``uuid`` or whatever)

Each value may be cast to any of the non-iterable methods documented above, by using
``env.ensure.methodname`` instead of ``env.methodname``, for example::

    env.tuple("VAR_NAME", "123,4356,235", converter=env.ensure.int) == (123, 4356, 235)
    env.set("VAR_NAME", "123,4356,235", converter=env.ensure.hex) == {"123", "4356", "235"}
    env.list("VAR_NAME", "a@b.com, b@c.com, def@ghi", env.ensure.email) == ['a@b.com', 'b@c.com', 'def@ghi']

``env.dict`` is slightly special in that it has arguments for ``key_converter`` and ``value_converter``
so that keys can have a different type to values. Both must still be homogenous::

    env.dict("VAR_NAME", "a=1, b=2, c=3", key_converter=env.ensure.hex, value_converter=env.ensure.int) == {'a': 1, 'c': 3, 'b': 2}


Django database URLs
^^^^^^^^^^^^^^^^^^^^

To facilitate an easier transition from `dj-database-url`_ or `django-environ`_,
there's support for reading a ``DATABASE_URL`` (or other named env var) into
the correct format to fit the `Django`_ ``DATABASES`` dictionaries::

    # default key is implicitly "DATABASE_URL"
    env.django_database_url(default="sqlite://:memory:")
    env.django_database_url("MY_DB", "postgres://user:pass@host:1234/dbname?conn_max_age=600")
    env.django_database_url("DATABASE_URL", "psql://user:pass@host:1234/dbname?conn_max_age=None&autocommit=True&atomic_requests=False")
    env.django_database_url("MYSQL_DBASE", "mysql://user:pass@host:1234/dbname?init_command=SET storage_engine=INNODB")
    env.django_database_url("PG_SOCKET", "postgres:////var/run/postgresql/db")

so you can ultimately do::

    DATABASES = {
        "default": env.django_database_url("postgres://localhost?conn_max_age=1"),
        "other_db": env.django_database_url("OTHER_DB", "sqlite://:memory:"),
    }

Differences from those libraries
''''''''''''''''''''''''''''''''

- Using the ``mssql`` scheme sets the driver to use `mssql-python`_.
- Providing a URL fragment (``?query_string...#x=10&y=True``) parses those key/values
  the same as the query string, but **always** puts them into the dictionary's global
  options, never into the ``'OPTIONS'`` key.
- the function ``django_database_url`` doesn't accept the ``conn_max_age`` and ``ssl_require`` used by `dj-database-url`_


Handling errors
---------------

Failing to successfully convert (or just validate) the value (whether from
the environment or from the fallback) immediately halts execution by raising
``EnvironmentCastError`` which is a subclass of ``ValueError``.

Failing to provide a **string** for a default/fallback value will
raise ``EnvironmentDefaultError`` which is *also* a subclass of ``ValueError``.

To catch any *anticipated* error then, is to::

    try:
        ...
    except (EnvironmentCastError, EnvironmentDefaultError) as e:
        ...

Checking for existence
----------------------

To find out if an environment variable is set, *regardless of it's value*, you can
use normal ``in`` testing::

    if "MY_KEY" in env:
        do_something_special()

which allows you to change behaviour based on seeing certain variables in the
running environment.

Tracking the requests
---------------------

Every access of an ``Environment`` (eg: the default ``env``) keeps an internal
log of the key requested + whether or not it was found and used in the environment.

These are available under ``env.used`` and ``env.fallbacks`` but may be accessed
together by iterating over the ``Environment`` in question, where each iteration
will yield a ``3-tuple`` of:

- environment variable name requested
- the ``default`` or *fallback* value
- a ``bool`` of whether or not the environment variable was used or whether the fallback was. ``True`` if found in the environment, ``False`` if the fallback was used.

For example, to output everything, you might do::

    from enviable import env, Environment
    import sys
    env.int("TEST", "4")
    myenv = Environment({"TESTING": "1"})
    myenv.bool("TESTING", "0")
    if __name__ == "__main__":
        for env_var_name, env_var_example, was_read_from_env in env:
            if was_read_from_env is True:
                sys.stdout.write("{} was in the environment\n".format(env_var_name))
            else:
                sys.stdout.write("{} was NOT in the environment, used default value of {}\n".format(env_var_name, env_var_example))

Note that in the above scenario, because ``env`` and ``myenv`` are different
instances with their own individual tracking, the request for ``TESTING`` will
not output, but ``TEST`` will.

Generating an env file
----------------------

To avoid carrying around stale environment variables on projects of longevity, the ``Environment``
provides a self-documenting mechanism to output all of the environment variables and
example values to stdout (or any stream), so you can just pipe to a file and you've got a usable env, give or
take adjusting the values for your machine::

    # my_cool_settings.py
    from enviable import env
    env.int("TEST", "4")
    env.hex("MY_COOL_VAR", "ABCDEF24")
    if __name__ == "__main__":
        env.print()

the above will print to stdout when you call ``python -m my_cool_settings`` like so::

    export MY_COOL_VAR='ABCDEF24'
    export TEST='4'

You can adjust the output to say, be `.env` file compatible like so::

    env.print("{key!s}='{value!s}'")

which would instead output::

    MY_COOL_VAR='ABCDEF24'
    TEST='4'

Only ``key`` and ``value`` format-kwargs are provided to the output template.

Running the tests
-----------------

Given a copy of the file ``enviable.py`` you ought to be able to do either of the following::

    $ python enviable.py
    $ python -m enviable

and see the output of the various tests I've bothered with. If mypy is installed,
it will also type-check the file.

TODO
----

- More tests

The license
-----------

It's `FreeBSD`_. There's should be a ``LICENSE`` file in the root of the repository, and in any archives.

.. _FreeBSD: http://en.wikipedia.org/wiki/BSD_licenses#2-clause_license_.28.22Simplified_BSD_License.22_or_.22FreeBSD_License.22.29
.. _Django: https://www.djangoproject.com/
.. _dj-database-url: https://github.com/jacobian/dj-database-url
.. _django-environ: https://github.com/joke2k/django-environ
.. _mssql-python: https://github.com/microsoft/mssql-django
