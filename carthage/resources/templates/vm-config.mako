<%
import uuid
model = model_in or object()
memory_mb = getattr(model, 'memory_mb', 8192)
cpus = getattr(model, 'cpus', 1)
nested_virt = getattr(model, 'nested_virt', False)
%>
<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>${name}</name>
  <uuid>${uuid.uuid4()}</uuid>
  <memory unit='KiB'>${memory_mb*1024}</memory>

  <vcpu >${cpus}</vcpu>
<cpu mode='host-model'>
     %if nested_virt:
     <feature policy='require' name='vmx' />
     %endif
</cpu>
<os>
    <type arch='x86_64' machine='pc-i440fx-2.6'>hvm</type>
    <loader type='pflash' readonly='yes'>/usr/share/OVMF/OVMF_CODE.fd</loader>                         
<nvram template='/usr/share/OVMF/OVMF_VARS.fd' />
    <bios useserial='yes'/>

    <bootmenu enable='yes'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
  </clock>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <devices>
    <emulator>/usr/bin/kvm</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2'/>
      <source file='${volume.path}'/>
      <target dev='hda' bus='scsi'/>
      <boot order='1'/>

    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <target dev='hdb' bus='scsi'/>
      <readonly/>

    </disk>
%if ci_data:
<disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <target  bus='scsi' dev='hdc' />
      <source file='${ci_data}' />
      <readonly/>

    </disk>
%endif
    <controller type='scsi' model='virtio-scsi' />
    %for i, link in links.items():
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
    <graphics type='spice' tlsPort='${console_port}'>
      <listen type='address' address='0.0.0.0' />
    </graphics>
    <sound model='ich6'>

    </sound>
    <video>
      <model type='qxl' ram='262144' vram='262144' vgamem='131072' heads='1' primary='yes'/>
      <address type='pci' domain='0x0000' bus='0x00' slot='0x02' function='0x0'/>
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
