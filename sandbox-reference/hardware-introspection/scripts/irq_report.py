#!/usr/bin/env python3
"""Report PCI device -> IRQ -> CPU affinity mappings from sysfs/procfs.

Works inside the Hermes Docker sandbox without lspci/dmidecode/lshw (not
installed, and no apt/sudo to install them) and without /proc/interrupts
(masked by Docker's default runc security profile). Everything here reads
directly from /sys/bus/pci/devices and /proc/irq, /sys/kernel/irq, which
ARE visible inside the sandbox even though the aggregated /proc/interrupts
table and lspci/dmidecode binaries are not.
"""
import os

PCI_ROOT = "/sys/bus/pci/devices"


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def irq_action(irq):
    return read(f"/sys/kernel/irq/{irq}/actions", "(no action / not readable)")


def irq_affinity(irq):
    return read(f"/proc/irq/{irq}/smp_affinity_list", "?")


def main():
    if not os.path.isdir(PCI_ROOT):
        print("No /sys/bus/pci/devices visible in this environment.")
        return

    rows = []
    for addr in sorted(os.listdir(PCI_ROOT)):
        dev_dir = os.path.join(PCI_ROOT, addr)
        irq = read(os.path.join(dev_dir, "irq"), "?")
        vendor = read(os.path.join(dev_dir, "vendor"), "?")
        device = read(os.path.join(dev_dir, "device"), "?")
        pci_class = read(os.path.join(dev_dir, "class"), "?")
        numa = read(os.path.join(dev_dir, "numa_node"), "?")
        rows.append((addr, irq, vendor, device, pci_class, numa))

    with_irq = [r for r in rows if r[1] not in ("0", "?", "")]
    no_irq = [r for r in rows if r[1] in ("0", "?", "")]

    print(f"{'PCI addr':<14}{'IRQ':<6}{'vendor':<9}{'device':<9}{'class':<12}{'numa':<6}{'irq action':<20}{'cpu affinity'}")
    print("-" * 100)
    for addr, irq, vendor, device, pci_class, numa in with_irq:
        print(f"{addr:<14}{irq:<6}{vendor:<9}{device:<9}{pci_class:<12}{numa:<6}"
              f"{irq_action(irq):<20}{irq_affinity(irq)}")

    print(f"\n{len(with_irq)} PCI devices with an assigned interrupt line, "
          f"{len(no_irq)} without (IRQ 0 / MSI-X-only devices allocate vectors "
          "dynamically and often don't show a fixed number here).")

    print("\nNote: the aggregated /proc/interrupts table and lspci/dmidecode/lshw "
          "binaries are not available in this sandbox (masked path / not "
          "installed respectively, no sudo to add them) -- this report is "
          "built entirely from /sys/bus/pci/devices/*, /sys/kernel/irq/*/actions, "
          "and /proc/irq/*/smp_affinity_list, which are all directly readable.")


if __name__ == "__main__":
    main()
