import re
from collections import defaultdict
from datetime import (
    date,
    time,
)
from datetime import datetime
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup
from django.test import override_settings
from tri_declarative import (
    Namespace,
    Shortcut,
    class_shortcut,
    getattr_path,
    setattr_path,
)
from tri_struct import Struct
from iommi._db_compat import field_defaults_factory
from iommi._web_compat import (
    Template,
    smart_str,
)
from iommi._web_compat import ValidationError
from iommi.base import (
    InvalidEndpointPathException,
    perform_ajax_dispatch,
)
from iommi.form import (
    AVOID_EMPTY_FORM,
    Action,
    FULL_FORM_FROM_REQUEST,
    Field,
    Form,
    INITIALS_FROM_GET,
    bool_parse,
    create_members_from_model,
    datetime_iso_formats,
    datetime_parse,
    decimal_parse,
    float_parse,
    get_name_field,
    int_parse,
    member_from_model,
    register_field_factory,
    render_attrs,
    render_template,
    url_parse,
)
from iommi.page import (
    Page,
)


from .compat import RequestFactory
from .helpers import (
    reindent,
    req,
)


def assert_one_error_and_matches_reg_exp(errors, reg_exp):
    error = list(errors)[0]
    assert len(errors) == 1
    assert re.search(reg_exp, error)


def test_declaration_merge():

    class MyForm(Form):
        class Meta:
            fields__foo = Field()

        bar = Field()

    form = MyForm()
    form.bind(request=None)

    assert {'foo', 'bar'} == set(form.fields.keys())


class MyTestForm(Form):
    party = Field.choice(choices=['ABC'], required=False)
    username = Field(
        is_valid=lambda form, field, parsed_data: (
            parsed_data.startswith(form.fields['party'].parsed_data.lower() + '_') if parsed_data is not None else None,
            'Username must begin with "%s_"' % form.fields['party'].parsed_data)
    )
    joined = Field.datetime(attr='contact__joined')
    a_date = Field.date()
    a_time = Field.time()
    staff = Field.boolean()
    admin = Field.boolean()
    manages = Field.multi_choice(choices=['DEF', 'KTH', 'LIU'], required=False)
    not_editable = Field.text(initial='Some non-editable text', editable=False)
    multi_choice_field = Field.multi_choice(choices=['a', 'b', 'c', 'd'], required=False)

    # TODO: tests for all shortcuts with required=False


def test_repr():
    assert '<iommi.form.Field foo>' == repr(Field(name='foo'))
    assert '<iommi.form.Field foo>' == str(Field(name='foo'))


def test_required_choice():
    class Required(Form):
        c = Field.choice(choices=[1, 2, 3])

    form = Required().bind(request=req('post', **{'-': ''}))

    # TODO: we assume this type of mode check without asserting in a lot of tests.. should fix this
    assert form.mode == FULL_FORM_FROM_REQUEST

    assert form.is_target()
    assert form.is_valid() is False
    assert form.fields['c'].errors == {'This field is required'}

    class NotRequired(Form):
        c = Field.choice(choices=[1, 2, 3], required=False)

    form = NotRequired().bind(request=req('post', **{'-': '', 'c': ''}))
    assert form.is_target()
    assert form.is_valid()
    assert form.fields['c'].errors == set()


def test_required():
    form = MyTestForm().bind(request=req('post', **{'-': ''}))
    assert form.is_target()
    assert form.is_valid() is False
    assert form.fields['a_date'].value is None
    assert form.fields['a_date'].errors == {'This field is required'}


def test_required_with_falsy_option():
    class MyForm(Form):
        foo = Field.choice(
            choices=[0, 1],
            parse=lambda string_value, **_: int(string_value)
        )
    form = MyForm().bind(request=req('post', **{'foo': '0', '-': ''}))
    assert form.fields.foo.value == 0
    assert form.fields.foo.errors == set()


def test_custom_raw_data():
    def my_form_raw_data(form, field, **_):
        del form
        del field
        return 'this is custom raw data'

    class MyForm(Form):
        foo = Field(raw_data=my_form_raw_data)

    form = MyForm().bind(request=req('post', **{'-': ''}))
    assert form.fields.foo.value == 'this is custom raw data'


def test_custom_raw_data_list():
    # This is useful for example when doing file upload. In that case the data is on request.FILES, not request.POST so we can use this to grab it from there

    def my_form_raw_data_list(form, field, **_):
        del form
        del field
        return ['this is custom raw data list']

    class MyForm(Form):
        foo = Field(
            raw_data_list=my_form_raw_data_list,
            is_list=True,
        )

    form = MyForm().bind(request=req('post', **{'-': ''}))
    assert form.fields.foo.value_list == ['this is custom raw data list']


def test_parse():
    # The spaces in the data are there to check that we strip input
    form = MyTestForm().bind(
        request=req('post', **{
            'party': 'ABC ',
            'username': 'abc_foo ',
            'joined': ' 2014-12-12 01:02:03  ',
            'staff': ' true',
            'admin': 'false ',
            'manages': ['DEF  ', 'KTH '],
            'a_date': '  2014-02-12  ',
            'a_time': '  01:02:03  ',
            'multi_choice_field': ['a', 'b'],
            '-': '',
        }),
    )

    assert [x.errors for x in form.fields.values()] == [set() for _ in form.fields]
    assert form.is_valid() is True
    assert form.fields['party'].parsed_data == 'ABC'
    assert form.fields['party'].value == 'ABC'

    assert form.fields['username'].parsed_data == 'abc_foo'
    assert form.fields['username'].value == 'abc_foo'

    assert form.fields['joined'].raw_data == '2014-12-12 01:02:03'
    assert form.fields['joined'].parsed_data == datetime(2014, 12, 12, 1, 2, 3)
    assert form.fields['joined'].value == datetime(2014, 12, 12, 1, 2, 3)

    assert form.fields['staff'].raw_data == 'true'
    assert form.fields['staff'].parsed_data is True
    assert form.fields['staff'].value is True

    assert form.fields['admin'].raw_data == 'false'
    assert form.fields['admin'].parsed_data is False
    assert form.fields['admin'].value is False

    assert form.fields['manages'].raw_data_list == ['DEF', 'KTH']
    assert form.fields['manages'].parsed_data_list == ['DEF', 'KTH']
    assert form.fields['manages'].value_list == ['DEF', 'KTH']

    assert form.fields['a_date'].raw_data == '2014-02-12'
    assert form.fields['a_date'].parsed_data == date(2014, 2, 12)
    assert form.fields['a_date'].value == date(2014, 2, 12)

    assert form.fields['a_time'].raw_data == '01:02:03'
    assert form.fields['a_time'].parsed_data == time(1, 2, 3)
    assert form.fields['a_time'].value == time(1, 2, 3)

    assert form.fields['multi_choice_field'].raw_data_list == ['a', 'b']
    assert form.fields['multi_choice_field'].parsed_data_list == ['a', 'b']
    assert form.fields['multi_choice_field'].value_list == ['a', 'b']
    assert form.fields['multi_choice_field'].is_list
    assert not form.fields['multi_choice_field'].errors
    assert form.fields['multi_choice_field'].rendered_value == 'a, b'

    instance = Struct(contact=Struct())
    form.apply(instance)
    assert instance == Struct(
        contact=Struct(joined=datetime(2014, 12, 12, 1, 2, 3)),
        party='ABC',
        staff=True,
        admin=False,
        username='abc_foo',
        manages=['DEF', 'KTH'],
        a_date=date(2014, 2, 12),
        a_time=time(1, 2, 3),
        not_editable='Some non-editable text',
        multi_choice_field=['a', 'b'],
    )


def test_parse_errors():
    def post_validation(form):
        form.add_error('General snafu')
    form = MyTestForm(
        post_validation=post_validation,
    ).bind(
        request=req('get', **dict(
            party='foo',
            username='bar_foo',
            joined='foo',
            staff='foo',
            admin='foo',
            a_date='fooasd',
            a_time='asdasd',
            multi_choice_field=['q'],
            **{'-': ''}
        )),
    )

    assert form.mode == FULL_FORM_FROM_REQUEST
    assert form.is_valid() is False

    assert form.errors == {'General snafu'}

    assert form.fields['party'].parsed_data == 'foo'
    assert form.fields['party'].errors == {'foo not in available choices'}
    assert form.fields['party'].value is None

    assert form.fields['username'].parsed_data == 'bar_foo'
    assert form.fields['username'].errors == {'Username must begin with "foo_"'}
    assert form.fields['username'].value is None

    assert form.fields['joined'].raw_data == 'foo'
    assert_one_error_and_matches_reg_exp(form.fields['joined'].errors, 'Time data "foo" does not match any of the formats .*')
    assert form.fields['joined'].parsed_data is None
    assert form.fields['joined'].value is None

    assert form.fields['staff'].raw_data == 'foo'
    assert form.fields['staff'].parsed_data is None
    assert form.fields['staff'].value is None

    assert form.fields['admin'].raw_data == 'foo'
    assert form.fields['admin'].parsed_data is None
    assert form.fields['admin'].value is None

    assert form.fields['a_date'].raw_data == 'fooasd'
    assert_one_error_and_matches_reg_exp(form.fields['a_date'].errors, "time data u?'fooasd' does not match format u?'%Y-%m-%d'")
    assert form.fields['a_date'].parsed_data is None
    assert form.fields['a_date'].value is None
    assert form.fields['a_date'].rendered_value == form.fields['a_date'].raw_data

    assert form.fields['a_time'].raw_data == 'asdasd'
    assert_one_error_and_matches_reg_exp(form.fields['a_time'].errors, "time data u?'asdasd' does not match format u?'%H:%M:%S'")
    assert form.fields['a_time'].parsed_data is None
    assert form.fields['a_time'].value is None

    assert form.fields['multi_choice_field'].raw_data_list == ['q']
    assert_one_error_and_matches_reg_exp(form.fields['multi_choice_field'].errors, "q not in available choices")
    assert form.fields['multi_choice_field'].parsed_data_list == ['q']
    assert form.fields['multi_choice_field'].value_list is None

    with pytest.raises(AssertionError):
        form.apply(Struct())


def test_initial_from_instance():
    assert Form(
        instance=Struct(a=Struct(b=7)),
        # TODO: this can't be rewritten as fields__a__b=Field() we should talk about that
        fields__foo=Field(attr='a__b'),
    ).bind(
        request=req('get'),
    ).fields.foo.initial == 7


def test_initial_list_from_instance():
    assert Form(
        instance=Struct(a=Struct(b=[7])),
        fields__foo=Field(attr='a__b', is_list=True),
    ).bind(
        request=req('get'),
    ).fields.foo.initial_list == [7]


def test_non_editable_from_initial():
    class MyForm(Form):
        foo = Field(editable=False, initial=':bar:')

    assert ':bar:' in MyForm().bind(request=req('get')).render_part()
    assert ':bar:' in MyForm().bind(request=req('post', **{'-': ''})).render_part()


def test_apply():
    form = Form(
        fields__foo=Field(initial=17, editable=False),
    ).bind(
        request=req('get'),
    )
    assert Struct(foo=17) == form.apply(Struct())


def test_show():
    assert list(Form(fields__foo=Field(show=True)).bind(request=req('get')).fields.keys()) == ['foo']
    assert list(Form(fields__foo=Field(show=False)).bind(request=req('get')).fields.keys()) == []
    assert list(Form(fields__foo=Field(show=lambda form, field: False)).bind(request=req('get')).fields.keys()) == []


def test_declared_fields():
    form = Form(
        fields=dict(
            foo=Field(show=True),
            bar=Field(show=False),
        ),
    ).bind(
        request=req('get'),
    )
    assert list(form.declared_fields.keys()) == ['foo', 'bar']
    assert list(form.fields.keys()) == ['foo']


def test_non_editable():
    assert Form(
        fields__foo=Field(editable=False),
    ).bind(
        request=req('get'),
    ).fields.foo.input_template == 'iommi/form/non_editable.html'


def test_non_editable_form():
    form = Form(
        editable=False,
        instance=Struct(foo=3, bar=4),
        fields=dict(
            foo=Field.integer(),
            bar=Field.integer(editable=False),
        ),
    ).bind(
        request=req('get', foo='1', bar='2'),
    )
    assert 3 == form.fields.foo.value
    assert 4 == form.fields.bar.value
    assert False is form.fields.foo.editable
    assert False is form.fields.bar.editable


def test_text_fields():
    assert '<input type="text" ' in Form(fields__foo=Field.text()).bind(request=req('get')).compact()
    assert '<textarea' in Form(fields__foo=Field.textarea()).bind(request=req('get')).compact()


def test_integer_field():
    assert Form(fields__foo=Field.integer(),).bind(request=req('get', foo=' 7  ')).fields.foo.parsed_data == 7

    actual_errors = Form(fields__foo=Field.integer()).bind(request=req('get', foo=' foo  ')).fields.foo.errors
    assert_one_error_and_matches_reg_exp(actual_errors, r"invalid literal for int\(\) with base 10: u?'foo'")


def test_float_field():
    assert Form(fields__foo=Field.float()).bind(request=req('get', foo=' 7.3  ')).fields.foo.parsed_data == 7.3
    assert Form(fields__foo=Field.float()).bind(request=req('get', foo=' foo  ')).fields.foo.errors == {"could not convert string to float: foo"}


def test_email_field():
    assert Form(fields__foo=Field.email()).bind(request=req('get', foo=' 5  ')).fields.foo.errors == {u'Enter a valid email address.'}
    assert Form(fields__foo=Field.email()).bind(request=req('get', foo='foo@example.com')).is_valid()


def test_phone_field():
    assert Form(fields__foo=Field.phone_number()).bind(request=req('get', foo=' asdasd  ')).fields.foo.errors == {u'Please use format +<country code> (XX) XX XX. Example of US number: +1 (212) 123 4567 or +1 212 123 4567'}
    assert Form(fields__foo=Field.phone_number()).bind(request=req('get', foo='+1 (212) 123 4567')).is_valid()
    assert Form(fields__foo=Field.phone_number()).bind(request=req('get', foo='+46 70 123 123')).is_valid()


def test_render_template_string():
    assert Form(fields__foo=Field(name='foo', template=None, template_string='{{ field.value }} {{ form.style }}')).bind(request=req('get', foo='7')).compact() == '7 compact\n' + AVOID_EMPTY_FORM.format('') + '\n'


def test_render_template():
    assert '<form' in Form(fields__foo=Field()).bind(request=req('get', foo='7')).render_part()


def test_render_on_dunder_html():
    form = Form(fields__foo=Field()).bind(request=req('get', foo='7'))
    assert form.table() == form.__html__()  # used by jinja2


def test_render_attrs():
    assert Form(fields__foo=Field(attrs={'foo': '1'})).bind(request=req('get', foo='7')).fields.foo.render_attrs() == ' foo="1"'
    assert Form(fields__foo=Field()).bind(request=req('get', foo='7')).fields.foo.render_attrs() == ' '
    assert render_attrs(dict(foo='"foo"')) == ' foo="&quot;foo&quot;"'


def test_render_attrs_new_style():
    assert Form(fields__foo=Field(name='foo', attrs__foo='1')).bind(request=req('get', foo='7')).fields.foo.render_attrs() == ' foo="1"'
    assert Form(fields__foo=Field(name='foo')).bind(request=req('get', foo='7')).fields.foo.render_attrs() == ' '


def test_render_attrs_bug_with_curly_brace():
    assert render_attrs(dict(foo='{foo}')) == ' foo="{foo}"'


def test_getattr_path():
    assert getattr_path(Struct(a=1), 'a') == 1
    assert getattr_path(Struct(a=Struct(b=2)), 'a__b') == 2
    with pytest.raises(AttributeError):
        getattr_path(Struct(a=2), 'b')

    assert getattr_path(Struct(a=None), 'a__b__c__d') is None


def test_setattr_path():
    assert getattr_path(setattr_path(Struct(a=0), 'a', 1), 'a') == 1
    assert getattr_path(setattr_path(Struct(a=Struct(b=0)), 'a__b', 2), 'a__b') == 2

    with pytest.raises(AttributeError):
        setattr_path(Struct(a=1), 'a__b', 1)


def test_multi_select_with_one_value_only():
    assert ['a'] == Form(
        fields__foo=Field.multi_choice(name='foo', choices=['a', 'b']),
    ).bind(request=req('get', foo=['a'])).fields.foo.value_list


def test_render_table():
    class MyForm(Form):
        foo = Field(
            input_container__attrs__class=dict(**{'###5###': True}),
            label_container__attrs__class=dict(**{'$$$11$$$': True}),
            help_text='^^^13^^^',
            display_name='***17***',
            id='$$$$5$$$$$'
        )

    table = MyForm().bind(request=req('get', foo='!!!7!!!')).table()
    assert '!!!7!!!' in table
    assert '###5###' in table
    assert '$$$11$$$' in table
    assert '^^^13^^^' in table
    assert '***17***' in table
    assert 'id="$$$$5$$$$$"' in table
    assert '<tr' in table

    # Assert that table is the default
    assert table == "%s" % MyForm().bind(request=req('get', foo='!!!7!!!'))


def test_heading():
    assert '<th colspan="2">#foo#</th>' in Form(fields__heading=Field.heading(display_name='#foo#')).bind(request=req('get')).table()


def test_info():
    form = Form(fields__foo=Field.info(value='#foo#')).bind(request=req('get'))
    assert form.is_valid() is True
    assert '#foo#' in form.table()


def test_radio():
    choices = [
        'a',
        'b',
        'c',
    ]
    req('get')
    form = Form(
        fields__foo=Field.radio(choices=choices),
    ).bind(
        request=req('get', foo='a'),
    )
    soup = BeautifulSoup(form.table(), 'html.parser')
    assert len(soup.find_all('input')) == len(choices) + 1  # +1 for AVOID_EMPTY_FORM
    assert [x.attrs['value'] for x in soup.find_all('input') if 'checked' in x.attrs] == ['a']


def test_hidden():
    soup = BeautifulSoup(Form(fields__foo=Field.hidden()).bind(request=req('get', foo='1')).table(), 'html.parser')
    assert [(x.attrs['type'], x.attrs['name'], x.attrs['value']) for x in soup.find_all('input')] == [('hidden', 'foo', '1'), ('hidden', '-', '')]


def test_hidden_with_name():
    class MyPage(Page):
        baz = Form(
            name='baz',
            fields__foo=Field.hidden(),
            attrs__method='get',
            default_child=False,
        )

    page = MyPage().bind(request=req('get', **{'baz/foo': '1'}))
    rendered_page = page.render_part()

    assert page.default_child
    assert not page.children().baz.default_child
    assert page.children().baz._is_bound
    assert page.children().baz.mode == INITIALS_FROM_GET

    soup = BeautifulSoup(rendered_page, 'html.parser')
    actual = [
        (x.attrs['type'], x.attrs.get('name'), x.attrs['value'])
        for x in soup.find_all('input')
        if x.attrs['type'] == 'hidden'
    ]
    expected = [
        ('hidden', 'baz/foo', '1'),
        ('hidden', '-baz', ''),
    ]
    assert actual == expected


def test_password():
    assert ' type="password" ' in Form(fields__foo=Field.password()).bind(request=req('get', foo='1')).table()


def test_choice_not_required():
    class MyForm(Form):
        foo = Field.choice(required=False, choices=['bar'])

    assert MyForm().bind(request=req('post', **{'foo': 'bar', '-': ''})).fields.foo.value == 'bar'
    assert MyForm().bind(request=req('post', **{'foo': '', '-': ''})).fields.foo.value is None


# def test_choice_default_parser():
#
#     class MyThing(object):
#         def __init__(self, name):
#             self.name = name
#
#         def __str__(self):
#             return self.name
#
#     a, b, c = MyThing('a'), MyThing('b'), MyThing('c')
#
#     class MyForm(Form):
#         foo = Field.choice(choices=[a, b, c])
#
#     assert MyForm(request=req('post', **{'foo': 'b', '-': ''})).fields.foo.value is b
#     assert MyForm(request=req('post', **{'foo': 'fisk', '-': ''})).fields.foo.errors == {'fisk not in available choices'}


def test_multi_choice():
    soup = BeautifulSoup(Form(
        fields__foo=Field.multi_choice(choices=['a'])
    ).bind(
        request=req('get', foo=['0']),
    ).table(), 'html.parser')
    assert [x.attrs['multiple'] for x in soup.find_all('select')] == ['']


@pytest.mark.django
def test_help_text_from_model():
    from tests.models import Foo

    assert Form(
        model=Foo,
        fields__foo=Field.from_model(model=Foo, field_name='foo'),
    ).bind(
        request=req('get', foo='1'),
    ).fields.foo.help_text == 'foo_help_text'


@pytest.mark.django_db
def test_help_text_from_model2():
    from .models import Foo, Bar
    # simple integer field
    assert Form.from_model(include=['foo'], model=Foo).bind(request=req('get', foo='1')).fields.foo.help_text == 'foo_help_text'

    # foreign key field
    Bar.objects.create(foo=Foo.objects.create(foo=1))
    form = Form.from_model(include=['foo'], model=Bar).bind(request=req('get'))
    assert form.fields.foo.help_text == 'bar_help_text'
    assert form.fields.foo.model is Foo


@pytest.mark.django_db
def test_multi_choice_queryset():
    from django.contrib.auth.models import User

    user = User.objects.create(username='foo')
    user2 = User.objects.create(username='foo2')
    user3 = User.objects.create(username='foo3')

    class MyForm(Form):
        foo = Field.multi_choice_queryset(attr=None, choices=User.objects.filter(username=user.username))

    assert [x.pk for x in MyForm().bind(request=req('get')).fields.foo.choices] == [user.pk]
    assert MyForm().bind(request=req('get', foo=smart_str(user2.pk))).fields.foo.errors == {'%s not in available choices' % user2.pk}
    assert MyForm().bind(request=req('get', foo=[smart_str(user2.pk), smart_str(user3.pk)])).fields.foo.errors == {'%s, %s not in available choices' % (user2.pk, user3.pk)}

    form = MyForm().bind(request=req('get', foo=[smart_str(user.pk)]))
    assert form.fields.foo.errors == set()
    result = form.render_part()
    assert str(BeautifulSoup(result, "html.parser").select('#id_foo')[0]) == '<input id="id_foo" multiple="" name="foo" type="hidden"/>'
    assert f'var data = [{{"id": {user.pk}, "text": "{user}"}}];' in result


@pytest.mark.django_db
def test_choice_queryset():
    from django.contrib.auth.models import User

    user = User.objects.create(username='foo')
    user2 = User.objects.create(username='foo2')
    User.objects.create(username='foo3')

    class MyForm(Form):
        foo = Field.choice_queryset(attr=None, choices=User.objects.filter(username=user.username))

    assert [x.pk for x in MyForm().bind(request=req('get')).fields.foo.choices] == [user.pk]
    assert MyForm().bind(request=req('get', foo=smart_str(user2.pk))).fields.foo.errors == {'%s not in available choices' % user2.pk}

    form = MyForm().bind(request=req('get', foo=[smart_str(user.pk)]))
    assert form.fields.foo.errors == set()
    result = form.render_part()
    print(result)
    assert str(BeautifulSoup(result, "html.parser").select('#id_foo')[0]) == '<input id="id_foo" name="foo" type="hidden"/>'
    assert f'var data = {{"id": {user.pk}, "text": "{user}"}};' in result


@pytest.mark.django_db
def test_choice_queryset_do_not_cache():
    from django.contrib.auth.models import User

    User.objects.create(username='foo')

    class MyForm(Form):
        foo = Field.choice_queryset(attr=None, choices=User.objects.all(), template='iommi/form/choice.html')

    # There is just one user, check that we get it
    form = MyForm().bind(request=req('get'))
    assert form.fields.foo.errors == set()
    assert str(BeautifulSoup(form.render_part(), "html.parser").select('select')[0]) == '<select id="id_foo" name="foo">\n<option value="1">foo</option>\n</select>'

    # Now create a new queryset, check that we get two!
    User.objects.create(username='foo2')
    form = MyForm().bind(request=req('get'))
    assert form.fields.foo.errors == set()
    assert str(BeautifulSoup(form.render_part(), "html.parser").select('select')[0]) == '<select id="id_foo" name="foo">\n<option value="1">foo</option>\n<option value="2">foo2</option>\n</select>'


@pytest.mark.django
def test_field_from_model():
    from tests.models import Foo

    class FooForm(Form):
        foo = Field.from_model(Foo, 'foo')

        class Meta:
            model = Foo

    assert FooForm().bind(request=req('get', foo='1')).fields.foo.value == 1
    assert not FooForm().bind(request=req('get', foo='asd')).is_valid()


@pytest.mark.django_db
def test_field_from_model_foreign_key_choices():
    from tests.models import Foo, Bar

    foo = Foo.objects.create(foo=1)
    foo2 = Foo.objects.create(foo=2)
    Bar.objects.create(foo=foo)
    Bar.objects.create(foo=foo2)

    class FooForm(Form):
        # Choices is a lambda here to avoid Field.field_choice_queryset grabbing the model from the queryset object
        foo = Field.from_model(Bar, 'foo', choices=lambda form, field, **_: Foo.objects.all())

    assert list(FooForm().bind(request=req('get')).fields.foo.choices) == list(Foo.objects.all())
    form = FooForm().bind(request=req('post', foo=str(foo2.pk)))
    bar = Bar()
    form.apply(bar)
    bar.save()
    assert bar.foo == foo2
    assert Bar.objects.get(pk=bar.pk).foo == foo2


@pytest.mark.django_db
def test_field_validate_foreign_key_does_not_exist():
    from tests.models import Foo, FieldFromModelForeignKeyTest

    foo = Foo.objects.create(foo=17)
    assert Foo.objects.count() == 1

    class MyForm(Form):
        class Meta:
            fields = Form.fields_from_model(model=FieldFromModelForeignKeyTest)

    assert MyForm().bind(request=req('post', foo_fk=foo.pk)).is_valid() is True
    assert MyForm().bind(request=req('post', foo_fk=foo.pk + 1)).is_valid() is False


@pytest.mark.django
def test_form_default_fields_from_model():
    from tests.models import Foo

    class FooForm(Form):
        class Meta:
            fields = Form.fields_from_model(model=Foo)
            fields__bar = Field.text(attr=None)

    assert set(FooForm().bind(request=req('get')).fields.keys()) == {'foo', 'bar'}
    assert FooForm().bind(request=req('get', foo='1')).fields.foo.value == 1
    assert not FooForm().bind(request=req('get', foo='asd')).is_valid()


@pytest.mark.django
@pytest.mark.filterwarnings("ignore:Model 'tests.foomodel' was already registered")
def test_field_from_model_factory_error_message():
    from django.db.models import Field as DjangoField, Model

    class CustomField(DjangoField):
        pass

    class FooModel(Model):
        foo = CustomField()

    with pytest.raises(AssertionError) as error:
        Field.from_model(FooModel, 'foo')

    acceptable_error_messages = [
        "No factory for <class 'tests.test_forms.CustomField'>. Register a factory with register_field_factory, you can also register one that returns None to not handle this field type",
        "No factory for <class 'tests.test_forms.test_field_from_model_factory_error_message.<locals>.CustomField'>. Register a factory with register_field_factory, you can also register one that returns None to not handle this field type",
    ]
    assert str(error.value) in acceptable_error_messages


@pytest.mark.django
@pytest.mark.filterwarnings("ignore:Model 'tests.foomodel' was already registered")
def test_field_from_model_required():
    from django.db.models import TextField, Model

    class FooModel(Model):
        a = TextField(blank=True, null=True)
        b = TextField(blank=True, null=False)
        c = TextField(blank=False, null=True)
        d = TextField(blank=False, null=False)

    assert not Field.from_model(FooModel, 'a').required
    assert not Field.from_model(FooModel, 'b').required
    assert not Field.from_model(FooModel, 'c').required
    assert Field.from_model(FooModel, 'd').required


@pytest.mark.django
@pytest.mark.filterwarnings("ignore:Model 'tests.foomodel' was already registered")
def test_field_from_model_label():
    from django.db.models import TextField, Model

    class FooModel(Model):
        a = TextField(verbose_name='FOOO bar FOO')

    assert Field.from_model(FooModel, 'a').display_name == 'FOOO bar FOO'


@pytest.mark.django_db
def test_form_from_model_valid_form():
    from tests.models import FormFromModelTest

    assert [x.value for x in Form.from_model(
        model=FormFromModelTest,
        include=['f_int', 'f_float', 'f_bool'],
    ).bind(
        request=req('get', f_int='1', f_float='1.1', f_bool='true'),
    ).fields.values()] == [
        1,
        1.1,
        True
    ]


@pytest.mark.django_db
def test_form_from_model_error_message_include():
    from tests.models import FormFromModelTest
    with pytest.raises(AssertionError) as e:
        Form.from_model(model=FormFromModelTest, include=['does_not_exist', 'another_non_existant__sub', 'f_float'], data=None)

    assert 'You can only include fields that exist on the model: another_non_existant__sub, does_not_exist specified but does not exist' == str(e.value)


@pytest.mark.django_db
def test_form_from_model_error_message_exclude():
    from tests.models import FormFromModelTest
    with pytest.raises(AssertionError) as e:
        Form.from_model(model=FormFromModelTest, exclude=['does_not_exist', 'does_not_exist_2', 'f_float'], data=None)

    assert 'You can only exclude fields that exist on the model: does_not_exist, does_not_exist_2 specified but does not exist' == str(e.value)


@pytest.mark.django
def test_form_from_model_invalid_form():
    from tests.models import FormFromModelTest

    actual_errors = [x.errors for x in Form.from_model(
        model=FormFromModelTest,
        exclude=['f_int_excluded'],
    ).bind(
        request=req('get', f_int='1.1', f_float='true', f_bool='asd', f_file='foo'),
    ).fields.values()]

    assert len(actual_errors) == 4
    assert {'could not convert string to float: true'} in actual_errors
    assert {u'asd is not a valid boolean value'} in actual_errors
    assert {"invalid literal for int() with base 10: '1.1'"} in actual_errors or {"invalid literal for int() with base 10: u'1.1'"} in actual_errors


@pytest.mark.django
def test_field_from_model_supports_all_types():
    from tests.models import Foo

    from django.db.models import fields
    not_supported = []
    blacklist = {
        'Field',
        'BinaryField',
        'FilePathField',
        'GenericIPAddressField',
        'IPAddressField',
        'NullBooleanField',
        'SlugField',
        'DurationField',
        'UUIDField'
    }
    field_type_names = [x for x in dir(fields) if x.endswith('Field') and x not in blacklist]

    for name in field_type_names:
        field_type = getattr(fields, name)
        try:
            Field.from_model(model=Foo, model_field=field_type())
        except AssertionError:  # pragma: no cover
            not_supported.append(name)

    assert not_supported == []


@pytest.mark.django
def test_field_from_model_blank_handling():
    from tests.models import Foo

    from django.db.models import CharField

    subject = Field.from_model(model=Foo, model_field=CharField(null=True, blank=False))
    assert True is subject.parse_empty_string_as_none

    subject = Field.from_model(model=Foo, model_field=CharField(null=False, blank=True))
    assert False is subject.parse_empty_string_as_none


@pytest.mark.django
def test_overriding_parse_empty_string_as_none_in_shortcut():
    from tests.models import Foo

    from django.db.models import CharField

    s = Shortcut(
        call_target=Field.text,
        parse_empty_string_as_none='foo',
    )
    # test overriding parse_empty_string_as_none
    x = member_from_model(
        cls=Field,
        model=Foo,
        model_field=CharField(blank=True),
        factory_lookup={CharField: s},
        factory_lookup_register_function=register_field_factory,
        defaults_factory=field_defaults_factory,
    )

    assert 'foo' == x.parse_empty_string_as_none


@pytest.mark.django_db
def test_field_from_model_foreign_key():
    from django.db.models import QuerySet
    from tests.models import Foo, FieldFromModelForeignKeyTest

    Foo.objects.create(foo=2)
    Foo.objects.create(foo=3)
    Foo.objects.create(foo=5)

    class MyForm(Form):
        c = Field.from_model(FieldFromModelForeignKeyTest, 'foo_fk')

    form = MyForm().bind(request=req('get'))
    choices = form.fields.c.choices
    assert isinstance(choices, QuerySet)
    assert set(choices) == set(Foo.objects.all())


@pytest.mark.django_db
def test_field_from_model_many_to_many():
    from django.db.models import QuerySet
    from tests.models import Foo, FieldFromModelManyToManyTest

    Foo.objects.create(foo=2)
    b = Foo.objects.create(foo=3)
    c = Foo.objects.create(foo=5)

    class MyForm(Form):
        foo_many_to_many = Field.from_model(FieldFromModelManyToManyTest, 'foo_many_to_many')

    form = MyForm().bind(request=req('get'))
    choices = form.fields.foo_many_to_many.choices

    assert isinstance(choices, QuerySet)
    assert set(choices) == set(Foo.objects.all())
    m2m = FieldFromModelManyToManyTest.objects.create()
    assert set(MyForm(instance=m2m).bind(request=req('get')).fields.foo_many_to_many.initial_list) == set()
    m2m.foo_many_to_many.add(b)
    assert set(MyForm(instance=m2m).bind(request=req('get')).fields.foo_many_to_many.initial_list) == {b}
    m2m.foo_many_to_many.add(c)
    assert set(MyForm(instance=m2m).bind(request=req('get')).fields.foo_many_to_many.initial_list) == {b, c}


@pytest.mark.django_db
def test_field_from_model_many_to_one_foreign_key():
    from tests.models import Bar

    assert set(Form.from_model(
        model=Bar,
        fields__foo__call_target=Field.from_model
    ).bind(
        request=req('get'),
    ).fields.keys()) == {'foo'}


@pytest.mark.django
def test_register_field_factory():
    from tests.models import FooField, RegisterFieldFactoryTest

    register_field_factory(FooField, lambda **kwargs: 7)

    assert Field.from_model(RegisterFieldFactoryTest, 'foo') == 7


def shortcut_test(shortcut, raw_and_parsed_data_tuples, normalizing=None, is_list=False):
    if normalizing is None:
        normalizing = []

    SENTINEL = object()

    def test_empty_string_data():
        f = Form(
            fields__foo=shortcut(required=False, ),
        ).bind(
            request=req('get', foo=''),
        )
        assert not f.get_errors()
        assert f.fields.foo.value is None
        assert f.fields.foo.value_list is None or f.fields.foo.value_list == []
        assert f.fields.foo.rendered_value == ''

    def test_empty_data():
        f = Form(
            fields__foo=shortcut(required=False, ),
        ).bind(
            request=req('get'),
        )
        assert not f.get_errors()
        assert f.fields.foo.value is None
        assert f.fields.foo.value_list is None or f.fields.foo.value_list == []

    def test_editable_false():
        f = Form(
            fields__foo=shortcut(required=False, initial=SENTINEL, editable=False),
        ).bind(
            request=req('get', foo='asdasasd'),
        )
        assert not f.get_errors()
        assert f.fields.foo.value is SENTINEL

    def test_editable_false_list():
        f = Form(
            fields__foo=shortcut(required=False, initial_list=[SENTINEL], editable=False),
        ).bind(
            request=req('get', foo='asdasasd'),
        )
        assert not f.get_errors()
        assert f.fields.foo.value_list == [SENTINEL]

    def test_roundtrip_from_initial_to_raw_string():
        for raw, initial in raw_and_parsed_data_tuples:
            form = Form(
                fields__foo=shortcut(required=True, initial=initial),
            ).bind(
                request=req('get'),
            )
            assert not form.get_errors()
            f = form.fields.foo
            assert not f.is_list
            assert initial == f.value
            assert raw == f.rendered_value, 'Roundtrip failed'

    def test_roundtrip_from_initial_to_raw_string_list():
        for raw, initial_list in raw_and_parsed_data_tuples:
            form = Form(
                fields__foo=shortcut(required=True, initial_list=initial_list),
            ).bind(
                request=req('get'),
            )
            assert not form.get_errors()
            f = form.fields.foo
            assert f.initial_list == initial_list
            assert f.is_list
            assert initial_list == f.value_list
            assert ', '.join([str(x) for x in raw]) == f.rendered_value, 'Roundtrip failed'

    def test_roundtrip_from_raw_string_to_initial():
        for raw, initial in raw_and_parsed_data_tuples:
            form = Form(
                fields__foo=shortcut(required=True, ),
            ).bind(
                request=req('get', foo=raw),
            )
            assert not form.get_errors(), 'input: %s' % raw
            f = form.fields.foo
            if f.is_list:
                assert f.raw_data_list == raw
                assert f.value_list == initial
                if initial:
                    assert [type(x) for x in f.value_list] == [type(x) for x in initial]
            else:
                assert f.raw_data == raw
                assert f.value == initial
                assert type(f.value) == type(initial)

    def test_normalizing():
        for non_normalized, normalized in normalizing:
            form = Form(
                fields__foo=shortcut(required=True, ),
            ).bind(
                request=req('get', foo=non_normalized),
            )
            assert not form.get_errors()
            assert form.fields.foo.rendered_value == normalized

    test_roundtrip_from_raw_string_to_initial()
    test_empty_string_data()
    test_empty_data()
    test_normalizing()

    if is_list:
        test_roundtrip_from_initial_to_raw_string_list()
        test_editable_false_list()
    else:
        test_roundtrip_from_initial_to_raw_string()
        test_editable_false()


def test_datetime():
    shortcut_test(
        Field.datetime,
        raw_and_parsed_data_tuples=[
            ('2001-02-03 12:13:14', datetime(2001, 2, 3, 12, 13, 14)),
        ],
        normalizing=[
            ('2001-02-03 12:13', '2001-02-03 12:13:00'),
            ('2001-02-03 12', '2001-02-03 12:00:00'),
        ],
    )


def test_date():
    shortcut_test(
        Field.date,
        raw_and_parsed_data_tuples=[
            ('2001-02-03', date(2001, 2, 3)),
        ],
    )


def test_time():
    shortcut_test(
        Field.time,
        raw_and_parsed_data_tuples=[
            ('12:34:56', time(12, 34, 56)),
        ],
        normalizing=[
            ('2:34:56', '02:34:56'),
        ],
    )


def test_integer():
    shortcut_test(
        Field.integer,
        raw_and_parsed_data_tuples=[
            ('123', 123),
        ],
        normalizing=[
            ('00123', '123'),
        ],
    )


def test_float():
    shortcut_test(
        Field.float,
        raw_and_parsed_data_tuples=[
            ('123.0', 123.0),
            ('123.123', 123.123),
        ],
        normalizing=[
            ('123', '123.0'),
            ('00123', '123.0'),
            ('00123.123', '123.123'),
        ],
    )


def test_multi_choice_shortcut():
    shortcut_test(
        Namespace(
            call_target=Field.multi_choice,
            choices=['a', 'b', 'c'],
        ),
        is_list=True,
        raw_and_parsed_data_tuples=[
            # (['b', 'c'], ['b', 'c']),
            ([], []),
        ],
    )


def test_choice_shortcut():
    shortcut_test(
        Namespace(
            call_target=Field.choice,
            choices=[1, 2, 3],
        ),
        raw_and_parsed_data_tuples=[
            ('1', 1),
        ],
    )


def test_render_custom():
    sentinel = '!!custom!!'
    assert sentinel in Form(fields__foo=Field(initial='not sentinel value', render_value=lambda form, field, value: sentinel)).table()


def test_boolean_initial_true():
    fields = dict(
        foo=Field.boolean(initial=True),
        bar=Field(required=False),
    )

    form = Form(fields=fields).bind(request=req('get'))
    assert form.fields.foo.value is True

    # If there are arguments, but not for key foo it means checkbox for foo has been unchecked.
    # Field foo should therefore be false.
    form = Form(fields=fields).bind(request=RequestFactory().get('/', dict(bar='baz', **{'-': ''})))
    assert form.fields.foo.value is False

    form = Form(fields=fields).bind(request=RequestFactory().get('/', dict(foo='on', bar='baz', **{'-': ''})))
    assert form.fields.foo.value is True


def test_file():
    class FooForm(Form):
        foo = Field.file(required=False)
    form = FooForm().bind(request=req('get', foo='1'))
    instance = Struct(foo=None)
    assert form.is_valid() is True
    form.apply(instance)
    assert instance.foo == '1'

    # Non-existent form entry should not overwrite data
    form = FooForm().bind(request=req('get', foo=''))
    assert form.is_valid(), {x.name: x.errors for x in form.fields}
    form.apply(instance)
    assert instance.foo == '1'

    form = FooForm().bind(request=req('get'))
    assert form.is_valid(), {x.name: x.errors for x in form.fields}
    form.apply(instance)
    assert instance.foo == '1'


@pytest.mark.django
def test_file_no_roundtrip():
    class FooForm(Form):
        foo = Field.file(is_valid=lambda form, field, parsed_data: (False, 'invalid!'))

    form = FooForm().bind(request=req('post', foo=b'binary_content_here'))
    assert form.is_valid() is False, form.get_errors()
    assert 'binary_content_here' not in form.render_part()


@pytest.mark.django
def test_mode_full_form_from_request():
    class FooForm(Form):
        foo = Field(required=True)
        bar = Field(required=True)
        baz = Field.boolean(initial=True)

    # empty POST
    form = FooForm().bind(request=req('post', **{'-': ''}))
    assert form.is_valid() is False
    assert form.errors == set()
    assert form.fields.foo.errors == {'This field is required'}
    assert form.fields['bar'].errors == {'This field is required'}
    assert form.fields['baz'].errors == set()  # not present in POST request means false

    form = FooForm().bind(request=req('post', **{
        'foo': 'x',
        'bar': 'y',
        'baz': 'false',
        '-': '',
    }))
    assert form.is_valid() is True
    assert form.fields['baz'].value is False

    # all params in GET
    form = FooForm().bind(request=req('get', **{'-': ''}))
    assert form.is_valid() is False
    assert form.fields.foo.errors == {'This field is required'}
    assert form.fields['bar'].errors == {'This field is required'}
    assert form.fields['baz'].errors == set()  # not present in POST request means false

    form = FooForm().bind(request=req('get', **{
        'foo': 'x',
        'bar': 'y',
        'baz': 'on',
        '-': '',
    }))
    assert not form.errors
    assert not form.fields.foo.errors

    assert form.is_valid() is True


def test_mode_initials_from_get():
    class FooForm(Form):
        foo = Field(required=True)
        bar = Field(required=True)
        baz = Field.boolean(initial=True)

    # empty GET
    form = FooForm().bind(request=req('get'))
    assert form.is_valid() is True

    # initials from GET
    form = FooForm().bind(request=req('get', foo='foo_initial'))
    assert form.is_valid() is True
    assert form.fields.foo.value == 'foo_initial'

    assert form.fields.foo.errors == set()
    assert form.fields['bar'].errors == set()
    assert form.fields['baz'].errors == set()


def test_form_errors_function():
    class MyForm(Form):
        foo = Field(is_valid=lambda **_: (False, 'field error'))

    def post_validation(form):
        form.add_error('global error')

    assert MyForm(
        post_validation=post_validation,
    ).bind(
        request=req('post', **{'-': '', 'foo': 'asd'}),
    ).get_errors() == {'global': {'global error'}, 'fields': {'foo': {'field error'}}}


@pytest.mark.django
@pytest.mark.filterwarnings("ignore:Model 'tests.foomodel' was already registered")
def test_null_field_factory():
    from django.db import models

    class ShouldBeNullField(models.Field):
        pass

    class FooModel(models.Model):
        should_be_null = ShouldBeNullField()
        foo = models.IntegerField()

    register_field_factory(ShouldBeNullField, None)

    form = Form.from_model(model=FooModel).bind(request=req('get'))
    assert list(form.fields.keys()) == ['foo']


@override_settings(DEBUG=True)
@pytest.mark.django_db
@pytest.mark.filterwarnings("ignore:Model 'tests.foomodel' was already registered")
@pytest.mark.parametrize(
    'kwargs', [
        dict(extra__endpoint_attrs=['username']),
        dict(extra__endpoint_attr='username'),
        dict(),
    ]
)
def test_choice_queryset_ajax_attrs_direct(kwargs):
    from django.contrib.auth.models import User

    User.objects.create(username='foo')
    user2 = User.objects.create(username='bar')

    class MyForm(Form):
        class Meta:
            name = 'form_name'
        username = Field.choice_queryset(choices=User.objects.all().order_by('username'), **kwargs)
        not_returning_anything = Field.integer()

    form = MyForm()
    actual = perform_ajax_dispatch(root=form, path='/field/username', value='ar', request=req('get'))
    assert actual == dict(results=[{'id': user2.pk, 'text': smart_str(user2)}], more=False, page=1)

    with pytest.raises(InvalidEndpointPathException) as e:
        perform_ajax_dispatch(root=form, path='/field/not_returning_anything', value='ar', request=req('get'))


@pytest.mark.django_db
@pytest.mark.filterwarnings("ignore:Model 'tests.foomodel' was already registered")
@pytest.mark.filterwarnings("ignore:Pagination may yield inconsistent results")
@pytest.mark.parametrize(
    'kwargs', [
        dict(),
        dict(fields__user__extra__endpoint_attrs=['username']),
        dict(fields__user__extra__endpoint_attr='username'),
    ]
)
def test_choice_queryset_ajax_attrs_foreign_key(kwargs):
    from django.contrib.auth.models import User
    from django.db import models
    from django.db.models import CASCADE

    class FooModel(models.Model):
        user = models.ForeignKey(User, on_delete=CASCADE)

    User.objects.create(username='foo')
    user2 = User.objects.create(username='bar')

    form = Form.from_model(model=FooModel, **kwargs).bind(request=req('get'))
    actual = perform_ajax_dispatch(root=form, path='/field/user', value='ar', request=req('get'))

    assert actual == dict(results=[{'id': user2.pk, 'text': smart_str(user2)}], more=False, page=1)


@override_settings(DEBUG=True)
def test_ajax_namespacing():
    class MyForm(Form):
        foo = Field(
            endpoint_handler=lambda **_: 'default',
            endpoint__bar=lambda **_: 'bar',
            endpoint__baaz=lambda **_: 'baaz',
        )

    request = req('get')
    form = MyForm()
    assert 'default' == perform_ajax_dispatch(root=form, path='/field/foo', value='ar', request=request)
    assert 'bar' == perform_ajax_dispatch(root=form, path='/field/foo/bar', value='ar', request=request)
    assert 'baaz' == perform_ajax_dispatch(root=form, path='/field/foo/baaz', value='ar', request=request)


@override_settings(DEBUG=True)
def test_ajax_config_and_validate():
    class MyForm(Form):
        foo = Field()
        bar = Field(post_validation=lambda field, **_: field.errors.add('FAIL'))

    request = req('get')
    form = MyForm()
    assert dict(
        name='foo',
    ) == perform_ajax_dispatch(root=form, path='/field/foo/config', value=None, request=request)

    assert dict(
        valid=True,
        errors=[]
    ) == perform_ajax_dispatch(root=form, path='/field/foo/validate', value='new value', request=request)

    assert dict(
        valid=False,
        errors=['FAIL']
    ) == perform_ajax_dispatch(root=form, path='/field/bar/validate', value='new value', request=request)


def test_is_empty_form_marker():
    request = req('get')
    assert AVOID_EMPTY_FORM.format('') in Form().bind(request=request).render_part()
    assert AVOID_EMPTY_FORM.format('') not in Form(is_full_form=False).bind(request=request).render_part()


@override_settings(DEBUG=True)
def test_custom_endpoint():
    class MyForm(Form):
        class Meta:
            endpoint__foo = lambda value, **_: 'foo' + value

    form = MyForm()
    assert 'foobar' == perform_ajax_dispatch(root=form, path='/foo', value='bar', request=req('get'))


def remove_csrf(html_code):
    csrf_regex = r'<input[^>]+csrfmiddlewaretoken[^>]+>'
    return re.sub(csrf_regex, '', html_code)


def test_render():
    class MyForm(Form):
        bar = Field()

    expected_html = """
        <form action="" method="post">
            <div>
                <tr class="required">
                    <td class="description_container">
                        <div class="formlabel">
                            <label for="id_bar">
                                Bar
                            </label>
                        </div>
                        <div class="formdescr">
                        </div>
                    </td>
                    <td>
                        <input id="id_bar" name="bar" type="text" value="">
                    </td>
                </tr>
                <input name="-" type="hidden" value=""/>
            </div>
            <div class="form_buttons clear">
                <div class="links">
                    <input accesskey="s" class="button" type="submit" value="Submit"></input>
                </div>
            </div>
        </form>
    """

    actual_html = remove_csrf(MyForm().bind(request=req('get')).render_part())
    prettified_expected = reindent(BeautifulSoup(expected_html, 'html.parser').prettify()).strip()
    prettified_actual = reindent(BeautifulSoup(actual_html, 'html.parser').prettify()).strip()
    assert prettified_expected == prettified_actual, "{}\n !=\n {}".format(prettified_expected, prettified_actual)


def test_bool_parse():
    for t in ['1', 'true', 't', 'yes', 'y', 'on']:
        assert bool_parse(t) is True

    for f in ['0', 'false', 'f', 'no', 'n', 'off']:
        assert bool_parse(f) is False


def test_decimal_parse():
    assert decimal_parse(string_value='1') == Decimal(1)

    with pytest.raises(ValidationError) as e:
        decimal_parse(string_value='asdasd')

    assert e.value.messages == ["Invalid literal for Decimal: u'asdasd'"] or e.value.messages == ["Invalid literal for Decimal: 'asdasd'"]


def test_url_parse():
    assert url_parse(string_value='https://foo.example') == 'https://foo.example'

    with pytest.raises(ValidationError) as e:
        url_parse(string_value='asdasd')

    assert e.value.messages == ['Enter a valid URL.']


def test_render_temlate_none():
    # noinspection PyTypeChecker
    assert render_template(request=None, template=None, context=None) == ''


def test_render_template_template_object():
    assert render_template(
        request=req('get'),
        context=dict(a='1'),
        template=Template(template_string='foo {{a}} bar')
    ) == 'foo 1 bar'


@pytest.mark.django
def test_action_render():
    import os
    RequestFactory().get('/', params={}, root_path=os.path.dirname(__file__))
    action = Action(display_name='Title', template='test_action_render.html')
    assert action.render_part() == 'tag=a display_name=Title'
    assert action.render_part() == action.__html__()  # used by jinja2


def test_action_repr():
    assert repr(Action(name='name', template='test_link_render.html')) == '<Action: name>'


def test_action_shortcut_icon():
    assert Action.icon('foo', display_name='title').render_part() == '<a ><i class="fa fa-foo"></i> title</a>'


def test_render_grouped_actions():
    req('get')  # needed when running in flask mode to have an app present
    form = Form(
        actions=dict(
            a=Action(display_name='a'),
            b=Action(display_name='b', show=lambda form, **_: False),
            q=Action(display_name='q', show=lambda form, **_: True),
            c=Action(display_name='c', group='group'),
            d=Action(display_name='d', group='group'),
            f=Action(display_name='f', group='group'),
            submit__show=False,
        ),
    ).bind(
        request=req('get'),
    )
    actual_html = form.render_actions()
    expected_html = """
    <div class="links">
         <div class="dropdown">
             <a id="id_dropdown_group" role="button" data-toggle="dropdown" data-target="#" href="/page.html" class="button button-primary">
                 group <i class="fa fa-lg fa-caret-down"></i>
             </a>

             <ul class="dropdown-menu" role="menu" aria-labelledby="id_dropdown_group">
                 <li role="presentation">
                     <a role="menuitem">c</a>
                 </li>

                 <li role="presentation">
                     <a role="menuitem">d</a>
                 </li>

                 <li role="presentation">
                     <a role="menuitem">f</a>
                 </li>
             </ul>
         </div>

         <a>a</a>
         <a>q</a>
    </div>"""

    prettified_expected = reindent(BeautifulSoup(expected_html, 'html.parser').prettify()).strip()
    prettified_actual = reindent(BeautifulSoup(actual_html, 'html.parser').prettify()).strip()
    assert prettified_expected == prettified_actual, "{}\n !=\n {}".format(prettified_expected, prettified_actual)


def test_show_prevents_read_from_instance():
    class MyForm(Form):
        foo = Field(show=False)

    MyForm(instance=object()).bind(request=req('get'))


def test_choice_post_validation_not_overwritten():
    def my_post_validation(field, **_):
        del field
        raise Exception('foobar')

    class MyForm(Form):
        foo = Field.choice(post_validation=my_post_validation, choices=[1, 2, 3])

    with pytest.raises(Exception) as e:
        MyForm().bind(request=req('get'))

    assert str(e.value) == 'foobar'


def test_choice_post_validation_chains_empty_choice_when_required_false():
    class MyForm(Form):
        foo = Field.choice(required=False, choices=[1, 2, 3])

    form = MyForm().bind(request=req('get'))

    assert list(form.fields.foo.choice_tuples()) == [
        form.fields.foo.empty_choice_tuple,
        (1, '1', '1', False),
        (2, '2', '2', False),
        (3, '3', '3', False),
    ]


def test_instance_set_earlier_than_evaluate_is_called():
    class MyForm(Form):
        foo = Field(initial=lambda form, **_: form.instance)

    MyForm()


@pytest.mark.django_db
def test_auto_field():
    from tests.models import Foo

    form = Form.from_model(model=Foo).bind(request=req('get'))
    assert 'id' not in form.fields

    form = Form.from_model(model=Foo, fields__id__show=True).bind(request=req('get'))
    assert 'id' in form.fields


def test_initial_set_earlier_than_evaluate_is_called():
    class MyForm(Form):
        foo = Field(
            extra__bar=lambda form, field, **_: field.initial
        )

    assert 17 == MyForm(instance=Struct(foo=17)).bind(request=req('get')).fields.foo.extra.bar


@pytest.mark.django_db
def test_field_from_model_path():
    from .models import Bar

    class FooForm(Form):
        baz = Field.from_model(Bar, 'foo__foo', help_text='another help text')

        class Meta:
            model = Bar

    assert FooForm().bind(request=req('get', baz='1')).fields.baz.attr == 'foo__foo'
    assert FooForm().bind(request=req('get', baz='1')).fields.baz.name == 'baz'
    assert FooForm().bind(request=req('get', baz='1')).fields.baz.value == 1
    assert FooForm().bind(request=req('get', baz='1')).fields.baz.help_text == 'another help text'
    assert not FooForm().bind(request=req('get', baz='asd')).is_valid()
    fake = Struct(foo=Struct(foo='1'))
    assert FooForm(instance=fake).bind(request=req('get')).fields.baz.initial == '1'
    assert FooForm(instance=fake).bind(request=req('get')).fields.baz.parse is int_parse


@pytest.mark.django_db
def test_field_from_model_subtype():
    from django.db import models

    class Foo(models.IntegerField):
        pass

    class FromModelSubtype(models.Model):
        foo = Foo()

    result = Field.from_model(model=FromModelSubtype, field_name='foo')

    assert result.parse is int_parse


@pytest.mark.django_db
def test_create_members_from_model_path():
    from .models import Foo, Bar

    class BarForm(Form):
        class Meta:
            fields = Form.fields_from_model(model=Bar, include=['foo__foo'])

    bar = Bar.objects.create(foo=Foo.objects.create(foo=7))
    form = BarForm(instance=bar).bind(request=req('get'))

    assert len(form.fields) == 1
    assert form.fields.foo.name == 'foo__foo'
    assert form.fields.foo.help_text == 'foo_help_text'


@pytest.mark.django_db
def test_create_members_from_model_reject_extra_arguments_to_member_params_by_member_name():
    from .models import Foo
    with pytest.raises(TypeError):
        create_members_from_model(default_factory=None, model=Foo, member_params_by_member_name=dict(foo=1), include=[])


@pytest.mark.django
def test_namespaces_do_not_call_in_templates():
    from django.template import RequestContext

    def raise_always():
        assert False

    assert Template('{{ foo }}').render(RequestContext(None, dict(foo=Namespace(call_target=raise_always))))


@pytest.mark.django
def test_choice_queryset_error_message_for_automatic_model_extraction():
    with pytest.raises(AssertionError) as e:
        Field.choice_queryset(choices=[])

    assert 'The convenience feature to automatically get the parameter model set only works for QuerySet instances or if you specify model_field' == str(e.value)


def test_datetime_parse():
    assert datetime_parse('2001-02-03 12') == datetime(2001, 2, 3, 12)

    bad_date = '091223'
    with pytest.raises(ValidationError) as e:
        datetime_parse(bad_date)

    expected = 'Time data "%s" does not match any of the formats %s' % (bad_date, ', '.join('"%s"' % x for x in datetime_iso_formats))
    assert expected == str(e.value) or [expected] == [str(x) for x in e.value]


@pytest.mark.django_db
def test_from_model_with_inheritance():
    from tests.models import FromModelWithInheritanceTest
    was_called = defaultdict(int)

    class MyField(Field):
        @classmethod
        @class_shortcut
        def float(cls, call_target=None, **kwargs):
            was_called['MyField.float'] += 1
            return call_target(**kwargs)

    class MyForm(Form):
        class Meta:
            member_class = MyField

    MyForm.from_model(
        model=FromModelWithInheritanceTest,
    ).bind(
        request=req('get'),
    )

    assert was_called == {
        'MyField.float': 1,
    }


@pytest.mark.django_db
def test_from_model_override_field():
    from tests.models import FormFromModelTest
    form = Form.from_model(
        model=FormFromModelTest,
        fields__f_float=Field(name='f_float'),
    ).bind(
        request=req('get'),
    )
    assert form.fields.f_float.parse is not float_parse


@pytest.mark.django_db
def test_get_name_field():
    from django.db.models import (
        Model,
        IntegerField,
        CharField,
        ForeignKey,
        CASCADE,
    )

    class Foo1(Model):
        a = IntegerField()
        name = CharField(max_length=255)

    class Bar1(Model):
        foo = ForeignKey(Foo1, on_delete=CASCADE)

    class Foo2(Model):
        a = IntegerField()
        fooname = CharField(max_length=255)
        name = CharField(max_length=255)

    class Bar2(Model):
        foo = ForeignKey(Foo2, on_delete=CASCADE)

    class Foo3(Model):
        name = IntegerField()
        fooname = CharField(max_length=255)

    class Bar3(Model):
        foo = ForeignKey(Foo3, on_delete=CASCADE)

    class Foo4(Model):
        fooname = CharField(max_length=255)
        barname = CharField(max_length=255)

    class Bar4(Model):
        foo = ForeignKey(Foo4, on_delete=CASCADE)

    class Foo5(Model):
        blabla = CharField(max_length=255)

    class Bar5(Model):
        foo = ForeignKey(Foo5, on_delete=CASCADE)

    class Foo6(Model):
        a = IntegerField()

    class Bar6(Model):
        foo = ForeignKey(Foo6, on_delete=CASCADE)

    assert get_name_field(Form.from_model(model=Bar1).bind(request=req('get')).fields.foo) == 'name'
    assert get_name_field(Form.from_model(model=Bar2).bind(request=req('get')).fields.foo) == 'name'
    assert get_name_field(Form.from_model(model=Bar3).bind(request=req('get')).fields.foo) == 'fooname'
    assert get_name_field(Form.from_model(model=Bar4).bind(request=req('get')).fields.foo) == 'fooname'
    assert get_name_field(Form.from_model(model=Bar5).bind(request=req('get')).fields.foo) == 'blabla'
    with pytest.raises(AssertionError):
        get_name_field(Form.from_model(model=Bar6).bind(request=req('get')).fields.foo)


def test_field_merge():
    form = Form(
        fields__foo={},
        instance=Struct(foo=1),
    ).bind(
        request=req('get'),
    )
    assert len(form.fields) == 1
    assert form.fields.foo.name == 'foo'
    assert form.fields.foo.value == 1


def test_override_doesnt_stick():
    class MyForm(Form):
        foo = Field()

    form = MyForm(fields__foo__show=False).bind(request=req('get'))
    assert len(form.fields) == 0

    form2 = MyForm().bind(request=req('get'))
    assert len(form2.fields) == 1


def test_override_shenanigans():
    class MyForm(Form):
        foo = Field()

    form = MyForm(fields__foo=Field.integer()).bind(request=req('get'))
    assert form.fields.foo.parse is int_parse

    form = MyForm(fields__foo__extra__hello=True).bind(request=req('get'))
    assert form.fields.foo.extra.hello is True
