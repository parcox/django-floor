# coding=utf-8
from __future__ import unicode_literals, absolute_import
import logging
import re

from django.http import QueryDict


try:
    from inspect import signature
except ImportError:
    from funcsigs import signature
from django import forms
from django.utils.translation import ugettext as _

from djangofloor.exceptions import InvalidRequest


__author__ = 'flanker'
REGISTERED_SIGNALS = {}
logger = logging.getLogger('djangofloor.request')


class RE(object):
    def __init__(self, value, caster=None):
        self.caster = caster
        self.regexp = re.compile(value)

    def __call__(self, value):
        matcher = self.regexp.match(value)
        if not matcher:
            raise ValueError
        value = matcher.group(1) if matcher.groups() else value
        return self.caster(value) if self.caster else value


class Choice(object):
    def __init__(self, values, caster=None):
        self.values = set(values)
        self.caster = caster

    def __call__(self, value):
        value = self.caster(value) if self.caster else value
        if value not in self.values:
            raise ValueError
        return value


class SerializedForm(object):
    """given a form and a `list` of `dict`, transforms the `dict` into a :class:`django.http.QueryDict` and initialize the form with it.

>>> class SimpleForm(forms.Form):
...    field = forms.CharField()
...
>>> x = SerializedForm(SimpleForm)
>>> form = x([{'field': 'object'}])
>>> form.is_valid()
True


    """

    def __init__(self, form_cls):
        self.form_cls = form_cls

    def __call__(self, value):
        """
        :param value:
        :type value: :class:`list` of :class:`dict`
        :return:
        :rtype: :class:`django.forms.Form`
        """
        query_dict = QueryDict('', mutable=True)
        for obj in value:
            query_dict.update({obj['name']: obj['value']})
        return self.form_cls(query_dict)


class SignalRequest(object):
    """ Store the username and the session key and must be supplied to any Python signal call.
    Can be constructed from a plain :class:`django.http.HttpRequest`.
    """
    def __init__(self, username, session_key, user_pk=None):
        self.username = username
        self.session_key = session_key
        self.user_pk = user_pk

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_request(cls, request):
        """ return a `SignalRequest` from a Django request
        :param request: standard Django request
        :type request: :class:`django.http.HttpRequest`
        :return:
        :rtype: :class:`djangofloor.decorators.SignalRequest`
        """
        username = request.user.get_username() if request.user and request.user.is_authenticated() else None
        user_pk = request.user.pk if request.user and request.user.is_authenticated() else None
        session_key = request.session.session_key if request.session else None
        return cls(username=username, session_key=session_key, user_pk=user_pk)


class CallWrapper(object):
    def __init__(self, fn, path=None):
        self.path = path
        self.function = fn
        self.__name__ = fn.__name__ if hasattr(fn, '__name__') else path

        # fetch signature to analyze arguments
        sig = signature(fn)
        self.required_arguments = []
        self.optional_arguments = []
        self.accept_kwargs = []
        self.argument_types = {}

        for key, param in sig.parameters.items():
            if key in ('request',):
                continue
            if param.kind == param.VAR_KEYWORD:  # corresponds to "fn(**kwargs)"
                self.accept_kwargs = True
            elif param.kind == param.VAR_POSITIONAL:  # corresponds to "fn(*args)"
                self.accept_kwargs = True
            elif param.default == param.empty:  # "fn(foo)" : kind = POSITIONAL_ONLY or POSITIONAL_OR_KEYWORD
                self.required_arguments.append(key)
                if param.annotation != param.empty and callable(param.annotation):
                    self.argument_types[key] = param.annotation
            else:
                self.optional_arguments.append(key)  # "fn(foo=bar)" : kind = POSITIONAL_OR_KEYWORD or KEYWORD_ONLY
                if param.annotation != param.empty and callable(param.annotation):
                    self.argument_types[key] = param.annotation

        if path is None:
            path = fn.__name__
        self.register(path)

    def register(self, path):
        raise NotImplementedError

    def check_kwargs(self, kwargs):
        logger.debug(self.path, kwargs)
        if not self.accept_kwargs:
            for arg_name in kwargs:
                if arg_name not in self.required_arguments and arg_name not in self.optional_arguments:
                    raise InvalidRequest(_('Unexpected argument: %(arg)s') % {'arg': arg_name})
        for arg_name in self.required_arguments:
            if arg_name not in kwargs:
                raise InvalidRequest(_('Required argument: %(arg)s') % {'arg': arg_name})
        for k, v in self.argument_types.items():
            try:
                if k in kwargs:
                    kwargs[k] = v(kwargs[k])
            except ValueError:
                raise InvalidRequest(_('Invalid value %(value)s for argument %(arg)s.') % {'arg': 'k', 'value': v})
        return kwargs

    def __call__(self, *args, **kwargs):
        return self.function(*args, **kwargs)


class RedisCallWrapper(CallWrapper):
    def __init__(self, fn, path=None, delayed=False, allow_from_client=True, auth_required=True):
        super(RedisCallWrapper, self).__init__(fn, path=path)
        self.allow_from_client = allow_from_client
        self.delayed = delayed
        self.auth_required = auth_required

    def register(self, path):
        REGISTERED_SIGNALS.setdefault(path, []).append(self)


def connect(fn=None, path=None, delayed=False, allow_from_client=True, auth_required=True):
    wrapped = lambda fn_: RedisCallWrapper(fn_, path=path, delayed=delayed, allow_from_client=allow_from_client, auth_required=auth_required)
    if fn is not None:
        wrapped = wrapped(fn)
    return wrapped
