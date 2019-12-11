enviable
========

:author: Keryn Knight
:version: 0.1.0

A small module for wrapping over environment variables (pulled from ``os.environ``)
which provides convenience methods to fetch and check various data types
(including iterables) in what I'd charitably hope is a sensible way.

Explicitly doesn't attempt to read from any ``.env`` or ``.envrc`` file, because that
doesn't describe valid examples or which things may/should be set into the
environment. It becomes an absolute pot-luck.

Tracks requested environment variables and their default/fallback/example values, and
whether or not the fallback was used. Never tracks the actual environment value.

If this package isn't to your liking, there's **plenty** of others, and I'm
largely suffering from *Not-Invented-Here syndrome*.

All methods exposed by the Environment accept a key and a default.

- The key is the environment variable to search for.
- The default **MUST** be a string, as it is subject to the same parsing as if it had
  been found in the environment, and thus serves as a documented example of a valid
  value to export as an environment variable. Enforced value documentation!

Example usage
-------------

A short series of some of the options::

    from enviable import env, Environment
    # the module level `env` is a premade `Environment` to work with and is
    # roughly the same as `myenv = Environment(os.environ)`

    DEBUG = env.bool("DEBUG", "off")
    GIT_HASH = env.hex("COMMIT_REF", "11ff3fe8ccfa4bbd9c144f68b84c80f6")
    SERVER_EMAIL = env.email("DEFAULT_EMAIL", "a@b.com")
    DYANMIC_IMPORT = env.importable("MY_MODULE", "path.to.mymodule")
    LOCAL_FILE = env.filepath("ACCESS_KEYS", "/valid/path/to/keys.json")
    API_URL = env.web_address("API_URL", "https://example.com/")

    # Iterables
    NUMBERS = env.tuple("NUMBERS", "(12,3,456)", converter=env.ensure.int)
    UNORDERED_NUMBERS = env.frozenset("NUMBERS", "12, 3, 456", converter=env.ensure.int)
    DICTISH = env.dict("WOO", "a=1, b=2, c=3", key_converter=env.ensure.text, value_converter=env.ensure.int)

    # Raw text *followed* by conversion
    DEEPER_DEBUG = env.ensure.bool(env.text("DEBUG_DEEPLY", "1"))

Handling errors
---------------

Failing to successfully convert (or just validate) the value (whether from
the environment or from the fallback) immediately halts execution by raising
``EnvironmentCastError`` which is a subclass of ``ValueError`` - it is recommended
that you only catch the former.

Types
-----

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
- dictionaries, with separate key & value conversion
- json

If `Django`_ is installed (sorry, I'm lazy) it should also handle:

- datetime
- date
- time

Casting on iterables
--------------------

Using any of ``env.tuple``, ``env.list``, ``env.set``, ``env.frozenset``,
or ``env.dict`` allows each parsed value to be validated and optionally cast,
with the caveat that the *iterable is homogenous* (that is, everything can be
converted to an ``int`` or a ``uuid`` or whatever)

``env.dict`` is slightly special in that it has arguments for ``key_converter`` and ``value_converter``
so that keys can have a different type to values. Both must still be homogenous.

Running the tests
-----------------

Given a copy of the file ``enviable.py`` you ought to be able to do::

    python enviable.py

and see the output of the various tests I've bothered with.

TODO
----

- Examples of every type / method
- More tests

The license
-----------

It's `FreeBSD`_. There's should be a ``LICENSE`` file in the root of the repository, and in any archives.

.. _FreeBSD: http://en.wikipedia.org/wiki/BSD_licenses#2-clause_license_.28.22Simplified_BSD_License.22_or_.22FreeBSD_License.22.29
.. _Django: https://www.djangoproject.com/
