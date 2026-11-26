# reframe on NixOS: add this file to `imports` in configuration.nix, put the
# settings from .env.example in /var/lib/reframe/reframe.env, and nixos-rebuild switch.
# To update, change `version` to a newer release and rebuild.
{ config, pkgs, ... }:
let
  version = "0.1.0";
  backend = config.virtualisation.oci-containers.backend;
in
{
  virtualisation.oci-containers.containers.reframe = {
    image = "ghcr.io/ory-the-human/reframe:${version}";
    ports = [ "1992:1992" ];
    volumes = [ "/var/lib/reframe:/data" ];
    environmentFiles = [ "/var/lib/reframe/reframe.env" ];
  };

  # `sudo reframe …` runs the CLI in the running container.
  environment.systemPackages = [
    (pkgs.writeShellScriptBin "reframe" ''
      exec ${backend} exec -i $([ -t 0 ] && echo -t) reframe reframe "$@"
    '')
  ];

  systemd.tmpfiles.rules = [ "d /var/lib/reframe 0700 root root -" ];
  networking.firewall.allowedTCPPorts = [ 1992 ];
}
