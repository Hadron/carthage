<%
model = model_in or object()
boot_order = 1
memory_mb = getattr(model, 'memory_mb', 8192)
cpus = getattr(model, 'cpus', 1)
nested_virt = getattr(model, 'nested_virt', False)
disk_cache = getattr(model, 'disk_cache', 'writethrough')
%>
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>${name}</name>
  <uuid>${uuid}</uuid>
  <memory unit='KiB'>${memory_mb*1024}</memory>

  <vcpu placement='static' >${cpus}</vcpu>
<cpu mode='host-model'>
     %if nested_virt:
     <feature policy='require' name='vmx' />
     %endif
</cpu>
<os firmware='efi'>
    <type arch='x86_64' machine='pc-i440fx-2.6'>hvm</type>
    <bios useserial='yes'/>

    <bootmenu enable='yes'/>
  </os>
  <features>
    <acpi/>
    <apic/>
    <hyperv mode='custom'>
      <relaxed state='on'/>
      <vapic state='on'/>
      <spinlocks state='on' retries='8191'/>
    </hyperv>
    <vmport state='off'/>
    <smm state='on'/>
  </features>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='hypervclock' present='yes'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <devices>
    <emulator>/usr/bin/kvm</emulator>
%for disk_num, disk in enumerate(disk_config):
    <disk type='${disk.source_type}' device='${disk.target_type}'>
      <driver name='qemu' type='${disk.driver}' cache='${disk.cache}' discard='unmap'/>
%if hasattr(disk, 'path'):
      <source ${disk.qemu_source}='${disk.path}'/>
%endif
      <target dev='hd${chr(ord('a')+disk_num)}' bus='${disk.bus}'/>
%if disk_num == 0 or disk.target_type == 'cdrom':
      <boot order='${boot_order}'/><%boot_order += 1 %>
%endif
%if getattr(disk, 'readonly', False):
      <readonly />
%endif

    </disk>
%endfor
<controller type='scsi' model='virtio-scsi' />
    <controller type='sata' index='0' />
%if getattr(model, 'hardware_tpm', True):
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
%endif
    %for i, link in links.items():
    <% if link.local_type: continue %>\
        <interface type='bridge'>
%if link.mac is not None:
      <mac address='${link.mac}'/>
      % endif
      <source bridge='${link.net_instance.bridge_name}'/>
      <model type='virtio'/>
      <target dev="${if_name(link.net)}" />
    </interface>
% endfor
<serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    %if console_needed:
    <channel type='spicevmc'>
      <target type='virtio' name='com.redhat.spice.0'/>
      <address type='virtio-serial' controller='0' bus='0' port='1'/>
    </channel>

    <input type='mouse' bus='ps2'/>
    <input type='keyboard' bus='ps2'/>
    <graphics type='spice' autoport='yes' >
      <listen type='address'  />
    </graphics>
    <sound model='ich9'>

    </sound>
    <video>
      <model type='qxl' ram='65536' vram='65536' vgamem='16384' heads='1' primary='yes'/>
    </video>
% endif
    <channel type='unix'>
      <target type='virtio' name='org.qemu.guest_agent.0'/>
    </channel>
    <memballoon model='virtio'>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x08' function='0x0'/>
    </memballoon>
  </devices>
%if console_needed:
<qemu:commandline>
    <qemu:env name='SPICE_DEBUG_ALLOW_MC' value='1'/>
  </qemu:commandline>
% endif


</domain>
