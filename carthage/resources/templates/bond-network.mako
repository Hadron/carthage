<%inherit file="network-base.mako" />
<%block name="link_block">
${parent.link_block()}
RequiredForOnline=no
</%block>
<%block name="network">
Bond=${link.member_of[0]}

</%block>
