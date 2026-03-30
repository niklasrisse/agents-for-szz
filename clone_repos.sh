#!/bin/bash
# Clone all repositories required for running experiments.
#
# Repositories come from two datasets:
#   - DS_LINUX: Linux kernel (repos/linux-kernel/)
#   - DS_GITHUB: Various GitHub projects (repos/<owner>/<repo>/)
#
# Usage:
#   bash clone_repos.sh          # Clone all repositories
#   bash clone_repos.sh --dry-run  # Show what would be cloned without cloning
#
# Note: This requires ~50+ GB of disk space and significant time for large repos
#       (especially linux, qemu, cpython, mesa).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPOS_DIR="$SCRIPT_DIR/repos"
DRY_RUN=false

if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=true
fi

# Each line: <local_path>|<git_url>
# Local path is relative to repos/
REPOS=(
    # DS_LINUX dataset (Linux kernel)
    "linux-kernel|https://github.com/torvalds/linux.git"

    # DS_GITHUB dataset — owner/repo structure
    "abarisain/dmix|https://github.com/abarisain/dmix.git"
    "adamretter/exist|https://github.com/adamretter/exist.git"
    "analogdevicesinc/linux|https://github.com/analogdevicesinc/linux.git"
    "andrewphorn/ClassiCube-Client|https://github.com/andrewphorn/ClassiCube-Client.git"
    "apache/bookkeeper|https://github.com/apache/bookkeeper.git"
    "AsherBond/qemu|https://github.com/AsherBond/qemu.git"
    "aviaryan/PopularMovies|https://github.com/aviaryan/PopularMovies.git"
    "benpicco/RIOT|https://github.com/benpicco/RIOT.git"
    "BenzoRoms/packages_providers_TelephonyProvider|https://github.com/BenzoRoms/packages_providers_TelephonyProvider.git"
    "Bioconductor/Rsamtools|https://github.com/Bioconductor/Rsamtools.git"
    "bminor/mesa-mesa|https://gitlab.freedesktop.org/mesa/mesa.git"
    "bvanassche/net-snmp|https://github.com/bvanassche/net-snmp.git"
    "CCI/cci|https://github.com/CCI/cci.git"
    "Chairshot215/android_kernel_lge_hammerhead-starship|https://github.com/Chairshot215/android_kernel_lge_hammerhead-starship.git"
    "christ66/git-plugin|https://github.com/christ66/git-plugin.git"
    "ClangBuiltLinux/linux|https://github.com/ClangBuiltLinux/linux.git"
    "clebertsuconic/activemq-artemis|https://github.com/clebertsuconic/activemq-artemis.git"
    "cmassiot/upipe|https://github.com/cmassiot/upipe.git"
    "code-saturne/code_saturne|https://github.com/code-saturne/code_saturne.git"
    "codinguser/gnucash-android|https://github.com/codinguser/gnucash-android.git"
    "computationalcore/smartcoins-wallet|https://github.com/computationalcore/smartcoins-wallet.git"
    "cpaasch/mptcp|https://github.com/cpaasch/mptcp.git"
    "crate/crate|https://github.com/crate/crate.git"
    "crobinso/libvirt|https://github.com/crobinso/libvirt.git"
    "crossminer/scava|https://github.com/crossminer/scava.git"
    "CyanogenMod/android_frameworks_base|https://github.com/CyanogenMod/android_frameworks_base.git"
    "DanVanAtta/triplea|https://github.com/DanVanAtta/triplea.git"
    "DaveMielke/liblouis|https://github.com/DaveMielke/liblouis.git"
    "diffplug/goomph|https://github.com/diffplug/goomph.git"
    "DigitalPebble/storm-crawler|https://github.com/DigitalPebble/storm-crawler.git"
    "dnk/mate-panel|https://github.com/dnk/mate-panel.git"
    "docwyatt2001/babeld|https://github.com/docwyatt2001/babeld.git"
    "doe300/jactiverecord|https://github.com/doe300/jactiverecord.git"
    "dougbinks/glfw|https://github.com/dougbinks/glfw.git"
    "DynamoRIO/dynamorio|https://github.com/DynamoRIO/dynamorio.git"
    "edoapra/simint-generator|https://github.com/edoapra/simint-generator.git"
    "ejurgensen/forked-daapd|https://github.com/ejurgensen/forked-daapd.git"
    "EnterpriseDB/pg_catcheck|https://github.com/EnterpriseDB/pg_catcheck.git"
    "eskalon/ProjektGG|https://github.com/eskalon/ProjektGG.git"
    "ethan-halsall/Simple-Kernel|https://github.com/ethan-halsall/Simple-Kernel.git"
    "fertrist/mishpaha-project-backend|https://github.com/fertrist/mishpaha-project-backend.git"
    "FFmpeg/FFmpeg|https://github.com/FFmpeg/FFmpeg.git"
    "fgast/opensips|https://github.com/fgast/opensips.git"
    "Frosticles/embann|https://github.com/Frosticles/embann.git"
    "garbagemule/MobArena|https://github.com/garbagemule/MobArena.git"
    "garbear/tyrquake|https://github.com/garbear/tyrquake.git"
    "gentoo/eudev|https://github.com/gentoo/eudev.git"
    "georchestra/georchestra|https://github.com/georchestra/georchestra.git"
    "gforney/smv|https://github.com/gforney/smv.git"
    "ghc/ghc|https://github.com/ghc/ghc.git"
    "gluster/glusterfs|https://github.com/gluster/glusterfs.git"
    "GNOME/gnome-builder|https://github.com/GNOME/gnome-builder.git"
    "gpac/gpac|https://github.com/gpac/gpac.git"
    "haproxy/haproxy|https://github.com/haproxy/haproxy.git"
    "huanghongxun/HMCL|https://github.com/huanghongxun/HMCL.git"
    "ilscipio/scipio-erp|https://github.com/ilscipio/scipio-erp.git"
    "imagej/imagej|https://github.com/imagej/imagej.git"
    "ImageMagick/ImageMagick|https://github.com/ImageMagick/ImageMagick.git"
    "INRIA/spoon|https://github.com/INRIA/spoon.git"
    "IronTeaPot/react-native-charts-wrapper|https://github.com/IronTeaPot/react-native-charts-wrapper.git"
    "jaamsim/jaamsim|https://github.com/jaamsim/jaamsim.git"
    "JetBrains/intellij-community|https://github.com/JetBrains/intellij-community.git"
    "JetBrains/xodus|https://github.com/JetBrains/xodus.git"
    "jiangxincode/Emma|https://github.com/jiangxincode/Emma.git"
    "johanmalm/jgmenu|https://github.com/johanmalm/jgmenu.git"
    "Jonathan-Ferguson/Quagga|https://github.com/Jonathan-Ferguson/Quagga.git"
    "joyent/libuv|https://github.com/joyent/libuv.git"
    "JuliaLang/julia|https://github.com/JuliaLang/julia.git"
    "jymigeon/libarchive|https://github.com/jymigeon/libarchive.git"
    "Kinetic/kinetic-c|https://github.com/Kinetic/kinetic-c.git"
    "lantw44/gsoc2013-evolution|https://github.com/lantw44/gsoc2013-evolution.git"
    "lemenkov/rtplib|https://github.com/lemenkov/rtplib.git"
    "libbun/libbun|https://github.com/libbun/libbun.git"
    "libreswan/libreswan|https://github.com/libreswan/libreswan.git"
    "libvirt/libvirt|https://github.com/libvirt/libvirt.git"
    "lumannnn/AudioRacer|https://github.com/lumannnn/AudioRacer.git"
    "m-x-d/KMQuake2|https://github.com/m-x-d/KMQuake2.git"
    "MaddTheSane/qemu|https://github.com/MaddTheSane/qemu.git"
    "magnumripper/JohnTheRipper|https://github.com/magnumripper/JohnTheRipper.git"
    "mangband/mangband|https://github.com/mangband/mangband.git"
    "markfasheh/ocfs2-tools|https://github.com/markfasheh/ocfs2-tools.git"
    "martintopholm/xping|https://github.com/martintopholm/xping.git"
    "MaTriXy/Paginize|https://github.com/MaTriXy/Paginize.git"
    "mesa3d/mesa|https://gitlab.freedesktop.org/mesa/mesa.git"
    "microchip-ais/linux|https://github.com/microchip-ais/linux.git"
    "mtaylor/activemq-artemis|https://github.com/mtaylor/activemq-artemis.git"
    "neomanu/NeoKernel-MT6589-A116|https://github.com/neomanu/NeoKernel-MT6589-A116.git"
    "netoptimizer/network-testing|https://github.com/netoptimizer/network-testing.git"
    "NFFT/nfft|https://github.com/NFFT/nfft.git"
    "nickvandewiele/RMG-Java|https://github.com/nickvandewiele/RMG-Java.git"
    "NightWhistler/PageTurner|https://github.com/NightWhistler/PageTurner.git"
    "open-keychain/open-keychain|https://github.com/open-keychain/open-keychain.git"
    "open-mpi/ompi|https://github.com/open-mpi/ompi.git"
    "openafs/openafs|https://github.com/openafs/openafs.git"
    "OpenChannelSSD/linux|https://github.com/OpenChannelSSD/linux.git"
    "openmicroscopy/bioformats|https://github.com/openmicroscopy/bioformats.git"
    "OpenSIPS/opensips|https://github.com/OpenSIPS/opensips.git"
    "openssl/openssl|https://github.com/openssl/openssl.git"
    "oVirt/ovirt-engine|https://github.com/oVirt/ovirt-engine.git"
    "palantir/atlasdb|https://github.com/palantir/atlasdb.git"
    "pantherb/setBfree|https://github.com/pantherb/setBfree.git"
    "pdg137/bigdecimal|https://github.com/pdg137/bigdecimal.git"
    "php/php-src|https://github.com/php/php-src.git"
    "pimtapath/kernel|https://github.com/pimtapath/kernel.git"
    "piotr-rusin/yule|https://github.com/piotr-rusin/yule.git"
    "polyglot-compiler/polyglot|https://github.com/polyglot-compiler/polyglot.git"
    "PreibischLab/BigStitcher|https://github.com/PreibischLab/BigStitcher.git"
    "prestodb/presto|https://github.com/prestodb/presto.git"
    "python/cpython|https://github.com/python/cpython.git"
    "qemu/qemu|https://github.com/qemu/qemu.git"
    "reactos/reactos|https://github.com/reactos/reactos.git"
    "Rockbox/rockbox|https://github.com/Rockbox/rockbox.git"
    "rstudio/rstudio|https://github.com/rstudio/rstudio.git"
    "rtrlib/rtrlib|https://github.com/rtrlib/rtrlib.git"
    "Sable/soot|https://github.com/Sable/soot.git"
    "sbabic/swupdate|https://github.com/sbabic/swupdate.git"
    "sctplab/usrsctp|https://github.com/sctplab/usrsctp.git"
    "scylladb/scylla-tools-java|https://github.com/scylladb/scylla-tools-java.git"
    "sdamashek/s2e|https://github.com/sdamashek/s2e.git"
    "sebastiaanschool/sebastiaanschool-Android|https://github.com/sebastiaanschool/sebastiaanschool-Android.git"
    "sebhtml/biosal|https://github.com/sebhtml/biosal.git"
    "sergev/pic32prog|https://github.com/sergev/pic32prog.git"
    "siemens/JMiniZinc|https://github.com/siemens/JMiniZinc.git"
    "simonsj/libssh|https://github.com/simonsj/libssh.git"
    "SimpleServer/SimpleServer|https://github.com/SimpleServer/SimpleServer.git"
    "sipi/dwmstatus|https://github.com/sipi/dwmstatus.git"
    "SlimRoms/kernel_htc_flounder|https://github.com/SlimRoms/kernel_htc_flounder.git"
    "sosy-lab/java-smt|https://github.com/sosy-lab/java-smt.git"
    "spring-projects/spring-boot|https://github.com/spring-projects/spring-boot.git"
    "spring-projects/spring-framework|https://github.com/spring-projects/spring-framework.git"
    "ssilverman/arduino-esp32|https://github.com/ssilverman/arduino-esp32.git"
    "starlightknight/jpcsp|https://github.com/starlightknight/jpcsp.git"
    "stephengold/Maud|https://github.com/stephengold/Maud.git"
    "structr/structr|https://github.com/structr/structr.git"
    "SupunArunoda/CPACEP|https://github.com/SupunArunoda/CPACEP.git"
    "SVB22/kernel_lenovo_msm8953|https://github.com/SVB22/kernel_lenovo_msm8953.git"
    "systemd/systemd|https://github.com/systemd/systemd.git"
    "TheHolyWaffle/TeamSpeak-3-Java-API|https://github.com/TheHolyWaffle/TeamSpeak-3-Java-API.git"
    "thinkaurelius/titan|https://github.com/thinkaurelius/titan.git"
    "topnotcher/gforce|https://github.com/topnotcher/gforce.git"
    "torvalds/linux|https://github.com/torvalds/linux.git"
    "tralamazza/mchck|https://github.com/tralamazza/mchck.git"
    "tswift242/tetris|https://github.com/tswift242/tetris.git"
    "Unidata/IDV|https://github.com/Unidata/IDV.git"
    "UniTime/unitime|https://github.com/UniTime/unitime.git"
    "vapier/qemu|https://github.com/vapier/qemu.git"
    "vividus-framework/vividus|https://github.com/vividus-framework/vividus.git"
    "vr100/github-api|https://github.com/vr100/github-api.git"
    "Wadeck/gitlab-oauth-plugin|https://github.com/Wadeck/gitlab-oauth-plugin.git"
    "ximion/limba|https://github.com/ximion/limba.git"
    "xwiki/xwiki-platform|https://github.com/xwiki/xwiki-platform.git"
    "yuriykulikov/AlarmClock|https://github.com/yuriykulikov/AlarmClock.git"
    "zack-vii/mdsplus|https://github.com/zack-vii/mdsplus.git"
    "zephyrproject-rtos/zephyr|https://github.com/zephyrproject-rtos/zephyr.git"
)

TOTAL=${#REPOS[@]}
CLONED=0
SKIPPED=0
FAILED=0

echo "========================================"
echo "Repository Cloning Script"
echo "========================================"
echo "Total repositories: $TOTAL"
echo "Target directory:   $REPOS_DIR"
echo "========================================"
echo

for entry in "${REPOS[@]}"; do
    LOCAL_PATH="${entry%%|*}"
    GIT_URL="${entry##*|}"
    FULL_PATH="$REPOS_DIR/$LOCAL_PATH"
    CURRENT=$((CLONED + SKIPPED + FAILED + 1))

    if [ -d "$FULL_PATH/.git" ] || [ -f "$FULL_PATH/.git" ]; then
        SKIPPED=$((SKIPPED + 1))
        echo "[$CURRENT/$TOTAL] SKIP (exists): $LOCAL_PATH"
        continue
    fi

    if [ "$DRY_RUN" = true ]; then
        echo "[$CURRENT/$TOTAL] WOULD CLONE: $GIT_URL -> $LOCAL_PATH"
        CLONED=$((CLONED + 1))
        continue
    fi

    echo "[$CURRENT/$TOTAL] Cloning: $GIT_URL -> $LOCAL_PATH"
    mkdir -p "$(dirname "$FULL_PATH")"

    if git clone "$GIT_URL" "$FULL_PATH" 2>&1 | tail -1; then
        CLONED=$((CLONED + 1))
        echo "  Done."
    else
        FAILED=$((FAILED + 1))
        echo "  FAILED to clone $GIT_URL"
    fi
    echo
done

echo "========================================"
echo "Summary"
echo "========================================"
echo "  Cloned:  $CLONED"
echo "  Skipped: $SKIPPED (already existed)"
echo "  Failed:  $FAILED"
echo "  Total:   $TOTAL"
echo "========================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
