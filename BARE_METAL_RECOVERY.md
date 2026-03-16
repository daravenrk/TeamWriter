# Dragonlair Bare Metal Recovery

This runbook restores Ubuntu + drivers + Dragonlair stack after disk or motherboard failure.

## What To Backup

Run this on the source host:

/home/daravenrk/dragonlair/bin/dragonlair_metal_backup

Default target:
- daravenrk@192.168.86.34:/backups/dragonlair-metal

Optional modes:
- Metadata only:
  /home/daravenrk/dragonlair/bin/dragonlair_metal_backup --metadata-only
- Dry run:
  /home/daravenrk/dragonlair/bin/dragonlair_metal_backup --dry-run

## Recovery Steps

1. Install Ubuntu on replacement hardware (same major version preferred).
2. Install base tools:
   sudo apt update
   sudo apt install -y rsync openssh-client docker.io docker-compose-plugin
3. Restore Dragonlair configs and scripts:
   /home/daravenrk/dragonlair/bin/dragonlair_stack_restore
4. Restore full root filesystem snapshot when required:
   /home/daravenrk/dragonlair/bin/dragonlair_stack_restore --with-models
   and/or use metadata and rootfs under backups/dragonlair-metal/HOST/TIMESTAMP
5. Reinstall GPU drivers (NVIDIA/ROCm) according to saved metadata:
   - metadata/nvidia-smi.txt
   - metadata/rocm-smi.txt
   - metadata/dkms-status.txt
6. Recreate partition layout if replacing disk:
   - Use metadata/sfdisk-*.txt and metadata/parted-*.txt
7. Rebuild bootloader if needed:
   sudo mount /dev/<root-partition> /mnt
   sudo mount /dev/<boot-partition> /mnt/boot            # if separate boot
   sudo mount /dev/<efi-partition> /mnt/boot/efi         # if UEFI
   for d in /dev /proc /sys /run; do sudo mount --bind $d /mnt$d; done
   sudo chroot /mnt
   grub-install /dev/<disk>
   update-grub
   update-initramfs -u -k all
   exit
8. Start services:
   - Ollama stack from /opt/ai-stack
   - Agent stack:
     /home/daravenrk/dragonlair/bin/agent-stack-up
9. Validate endpoints and dashboard:
   - curl -sS http://127.0.0.1:11434/api/tags
   - curl -sS http://127.0.0.1:11435/api/tags
   - curl -sS http://127.0.0.1:11888/api/health

## Notes

- For motherboard changes, GPU driver reinstall is usually required.
- Keep Ubuntu release and kernel family close to original to reduce driver mismatch.
- Run periodic test restores on a spare disk or VM to verify recoverability.
