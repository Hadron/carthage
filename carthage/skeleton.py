# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import argparse
from pathlib import Path
from .utils import carthage_main_argparser, mako_lookup

SKELETONS = ('layout',
             )

__all__ = []

def skeleton_subparser_setup(parser: argparse.ArgumentParser) ->argparse.ArgumentParser:
    action = parser.add_subparsers(title='layout', dest='skel',
                                   help='which layout to select', 
                                   required=True)
    for skel in SKELETONS:
        plugin_uri = mako_lookup.adjust_uri(skel+'/carthage_plugin.yml', 'skeletons/')
        plugin_template = mako_lookup.get_template(plugin_uri+'.mako')
        help_text = plugin_template.get_def('help').render()
        subparser = action.add_parser(skel,
                                         help=help_text)
        if plugin_template.has_def('arguments'):
            arguments = plugin_template.get_def('arguments')
            # allow template to adjust arguments.
            arguments.render(parser=subparser)
    return parser

__all__ += ['skeleton_subparser_setup']

def subrender_wrapper(context, relative_in, relative_out:Path, absolute_out:Path):
    def subrender(template, output=None, **kwargs):
        new_ctx = context.copy()
        new_ctx.update(kwargs)
        del new_ctx['subrender']
        template_uri = mako_lookup.adjust_uri(template, relative_in)
        our_template = mako_lookup.get_template(template_uri+'.mako')
        if output is None and our_template.has_def('output'):
            output_text = our_template.get_def('output').render(**new_ctx)
            output = output_text.strip()
        if output is None:
            assert not template.startswith('/')
            output = relative_out.joinpath(template)
        else:
            output = Path(output)
            if output.is_absolute(): # map / to  absolute_out
                output = output.relative_to('/')
                output = absolute_out.joinpath(output)
            else:
                output = relative_out.joinpath(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        new_ctx['subrender'] = subrender_wrapper(new_ctx, our_template.uri,
                                                 output.parent, absolute_out)
        output.write_text(our_template.render(**new_ctx))
        return ""
    return subrender

def render_skeleton(skeleton, output_top, args, **kwargs):
    context = kwargs
    context['args'] = args
    assert skeleton in SKELETONS
    context['subrender'] = None
    subrender = subrender_wrapper(context,
                                  'skeletons/'+skeleton+'/',
                                  Path(output_top), Path(output_top))
    subrender('carthage_plugin.yml')
    
    
__all__ += ['render_skeleton']

def package_to_dir(p):
    return p.replace('.','/')

