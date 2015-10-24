# -*- coding: utf-8 -*-
import os
import pystache
import re
import subprocess
import yaml

from postal.text.tokenize import tokenize, tokenize_raw, token_types
from collections import OrderedDict
from itertools import ifilter

FORMATTER_GIT_REPO = 'https://github.com/OpenCageData/address-formatting'


class AddressFormatter(object):
    '''
    Approximate Python port of lokku's Geo::Address::Formatter

    Usage:
        address_formatter = AddressFormatter()
        components = {
            'house': u'Anticafé',
            'addr:housenumber': '2',
            'addr:street': u'Calle de la Unión',
            'addr:postcode': '28013',
            'addr:city': u'Madrid',
        }
        address_formatter.format_address('es', components)
    '''

    whitespace_component_regex = re.compile('[\r\n]+[\s\r\n]*')

    splitter = ' | '

    separator_tag = 'SEP'
    field_separator_tag = 'FSEP'

    HOUSE = 'house'
    HOUSE_NUMBER = 'house_number'
    ROAD = 'road'
    SUBURB = 'suburb'
    CITY = 'city'
    STATE = 'state'
    STATE_DISTRICT = 'state_district'
    POSTCODE = 'postcode'
    COUNTRY = 'country'

    aliases = OrderedDict([
        ('name', HOUSE),
        ('addr:housename', HOUSE),
        ('addr:housenumber', HOUSE_NUMBER),
        ('addr:house_number', HOUSE_NUMBER),
        ('addr:street', ROAD),
        ('addr:city', CITY),
        ('addr:locality', CITY),
        ('addr:municipality', CITY),
        ('addr:hamlet', CITY),
        ('addr:suburb', SUBURB),
        ('addr:neighbourhood', SUBURB),
        ('addr:neighborhood', SUBURB),
        ('addr:district', SUBURB),
        ('addr:state', STATE),
        ('addr:province', STATE),
        ('addr:region', STATE),
        ('addr:postal_code', POSTCODE),
        ('addr:postcode', POSTCODE),
        ('addr:country', COUNTRY),
        ('street', ROAD),
        ('street_name', ROAD),
        ('residential', ROAD),
        ('hamlet', CITY),
        ('neighborhood', SUBURB),
        ('neighbourhood', SUBURB),
        ('city_district', SUBURB),
        ('state_code', STATE),
        ('country_name', COUNTRY),
        ('postal_code', POSTCODE),
        ('post_code', POSTCODE),
    ])

    MINIMAL_COMPONENT_KEYS = [
        (ROAD, HOUSE_NUMBER),
        (ROAD, HOUSE),
        (ROAD, POSTCODE)
    ]

    def __init__(self, scratch_dir='/tmp', splitter=None):
        if splitter is not None:
            self.splitter = splitter

        self.formatter_repo_path = os.path.join(scratch_dir, 'address-formatting')
        self.clone_repo()
        self.load_config()

    def clone_repo(self):
        subprocess.check_call(['rm', '-rf', self.formatter_repo_path])
        subprocess.check_call(['git', 'clone', FORMATTER_GIT_REPO, self.formatter_repo_path])

    def load_config(self):
        config = yaml.load(open(os.path.join(self.formatter_repo_path,
                                'conf/countries/worldwide.yaml')))
        for key, value in config.items():
            if hasattr(value, 'items'):
                address_template = value.get('address_template')
                if address_template:
                    value['address_template'] = self.add_suburb_tags(address_template)

                post_format_replacements = value.get('postformat_replace')
                if post_format_replacements:
                    value['postformat_replace'] = [[pattern, replacement.replace('$', '\\')] for pattern, replacement in post_format_replacements]
            else:
                address_template = value
                config[key] = self.add_suburb_tags(value)
        self.config = config

    def component_aliases(self):
        self.aliases = OrderedDict()
        self.aliases.update(self.osm_aliases)
        components = yaml.load_all(open(os.path.join(self.formatter_repo_path,
                                   'conf', 'components.yaml')))
        for c in components:
            name = c['name']
            for a in c.get('aliases', []):
                self.aliases[a] = name

    def replace_aliases(self, components):
        for k in components.keys():
            new_key = self.aliases.get(k)
            if new_key and new_key not in components:
                components[new_key] = components.pop(k)

    def country_template(self, c):
        return self.config.get(c, self.config['default'])

    post_suburb_keys = re.compile('|'.join((CITY, STATE, STATE_DISTRICT, POSTCODE, COUNTRY)), re.I)

    def add_suburb_tags(self, template):
        suburb_included = 'suburb' in template
        new_components = []
        for line in template.split('\n'):
            post_suburb = self.post_suburb_keys.search(line)
            new_components.append(line.rstrip('\n'))
            if u'road' in line and not suburb_included and not post_suburb:
                new_components.append(u'{{{suburb}}}')
        return u'\n'.join(new_components)

    def render_template(self, template, components, tagged=False):
        def render_first(text):
            text = pystache.render(text, **components)
            splits = (e.strip() for e in text.split('||'))
            selected = next(ifilter(bool, splits), '')
            return selected

        output = pystache.render(template, first=render_first,
                                 **components).strip()

        values = self.whitespace_component_regex.split(output)

        splitter = self.splitter if not tagged else ' {}/{} '.format(self.splitter.strip(), self.field_separator_tag)

        values = [self.strip_component(val, tagged=tagged) for val in values]

        output = splitter.join([
            val for val in values if val.strip()
        ])

        return output

    def minimal_components(self, components):
        for component_list in self.MINIMAL_COMPONENT_KEYS:
            if all((c in components for c in component_list)):
                return True
        return False

    def apply_replacements(self, template, components):
        if not template.get('replace'):
            return
        for key in components.keys():
            value = components[key]
            for regex, replacement in template['replace']:
                value = re.sub(regex, replacement, value)
                components[key] = value

    def post_replacements(self, template, text):
        components = []
        seen = set()
        for component in text.split(self.splitter):
            component = component.strip()
            if component not in seen:
                components.append(component)
                seen.add(component)
        text = self.splitter.join(components)
        post_format_replacements = template.get('postformat_replace')
        if post_format_replacements:
            for regex, replacement in post_format_replacements:
                text = re.sub(regex, replacement, text)
        return text

    def tag_template_separators(self, template):
        template = re.sub(r'},', '}} ,/{} '.format(self.separator_tag), template)
        template = re.sub(r'}-', '}} -/{} '.format(self.separator_tag), template)
        template = re.sub(r' - ', ' -/{} '.format(self.separator_tag), template)
        return template

    def strip_component(self, value, tagged=False):
        if not tagged:
            comma = token_types.COMMA.value
            hyphen = token_types.HYPHEN.value

            start = end = 0
            tokens = tokenize_raw(value.strip())
            for token_start, token_length, token_type in tokens:
                start = token_start
                if token_type not in (comma, hyphen):
                    break
                else:
                    start = token_start + token_length

            for token_start, token_length, token_type in reversed(tokens):
                end = token_start + token_length
                if token_type not in (comma, hyphen):
                    break
                else:
                    end = token_start

            return value[start:end]
        else:
            start = end = 0
            tokens = value.split()

            separator_tag = self.separator_tag

            for i, t in enumerate(tokens):
                t, c = t.rsplit('/', 1)
                start = i
                if c != separator_tag:
                    break
                else:
                    start = i + 1

            num_tokens = len(tokens)

            for j, t in enumerate(reversed(tokens)):
                t, c = t.rsplit('/', 1)
                end = num_tokens - j
                if c != separator_tag:
                    break
                else:
                    end = num_tokens - j - 1

            return u' '.join(tokens[start:end])

    def format_address(self, country, components, minimal_only=True, tag_components=True, replace_aliases=True):
        template = self.config.get(country.upper())
        if not template:
            return None
        template_text = template['address_template']
        if replace_aliases:
            self.replace_aliases(components)

        if minimal_only and not self.minimal_components(components):
            return None

        self.apply_replacements(template, components)

        if tag_components:
            template_text = self.tag_template_separators(template_text)
            components = {k: u' '.join([u'{}/{}'.format(t.replace(' ', ''), k.replace(' ', '_'))
                                        for t, c in tokenize(v)])
                          for k, v in components.iteritems()}

        text = self.render_template(template_text, components, tagged=tag_components)

        text = self.post_replacements(template, text)
        return text
