      {
        "password": "aces",
        "user": "aces",
        "port": self.console_port.port,
        "host": sh.hostname('--fqdn', _encoding = 'utf-8').strip(),
        "description": self.full_name,
        "label": self.full_name,
        "type": "spice",
        "ca": self.vm_ca,
      }
