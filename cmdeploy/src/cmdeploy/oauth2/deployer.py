from pyinfra.operations import files, pip, systemd

from cmdeploy.basedeploy import Deployer, get_resource


class OAuth2Deployer(Deployer):
    service = "chatmail-oauth2"
    
    def __init__(self, config):
        self.config = config
        self.need_restart = False
    
    def install(self):
        """Install OAuth2 dependencies."""
        if not self.config.oauth2_enabled:
            return
        
        # Install Python dependencies in venv
        pip.packages(
            name="Install OAuth2 dependencies",
            packages=["Flask", "authlib", "qrcode[pil]"],
            virtualenv="/usr/local/lib/chatmaild/venv",
        )
    
    def configure(self):
        """Configure OAuth2 service."""
        if not self.config.oauth2_enabled:
            return
        
        # Install systemd service
        self.need_restart |= files.put(
            name="Setup chatmail-oauth2.service",
            src=get_resource("systemd/chatmail-oauth2.service"),
            dest=f"/etc/systemd/system/{self.service}.service",
            user="root",
            group="root",
            mode="644",
        ).changed
    
    def activate(self):
        """Start and enable OAuth2 service."""
        if not self.config.oauth2_enabled:
            # Ensure service is stopped if OAuth2 is disabled
            systemd.service(
                name=f"Stop {self.service}",
                service=f"{self.service}.service",
                running=False,
                enabled=False,
            )
            return
        
        systemd.service(
            name=f"Start and enable {self.service}",
            service=f"{self.service}.service",
            running=True,
            enabled=True,
            restarted=self.need_restart,
            daemon_reload=True,
        )
        self.need_restart = False
