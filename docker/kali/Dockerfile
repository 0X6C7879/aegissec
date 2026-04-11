FROM kalilinux/kali-rolling

ARG INSTALL_CTF_TOOLS=1
ARG CTF_INSTALL_MODE=all
ARG INSTALL_SKILL_TOOLS=1
ARG SKILL_TOOL_PROFILE=core
ARG CTF_INSTALLER_SHA=unknown

ENV DEBIAN_FRONTEND=noninteractive
ENV PATH="/root/.local/bin:/root/go/bin:/root/.ctf-tools/venv/bin:/root/.aegissec-skill-tools/venv/bin:${PATH}"

LABEL aegissec.install_ctf_tools="${INSTALL_CTF_TOOLS}"
LABEL aegissec.ctf_install_mode="${CTF_INSTALL_MODE}"
LABEL aegissec.install_skill_tools="${INSTALL_SKILL_TOOLS}"
LABEL aegissec.skill_tool_profile="${SKILL_TOOL_PROFILE}"
LABEL aegissec.ctf_installer_sha="${CTF_INSTALLER_SHA}"

RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y -o Acquire::Retries=5 --fix-missing \
        bash \
        build-essential \
        ca-certificates \
        cargo \
        curl \
        git \
        golang-go \
        gnupg \
        jq \
        libcap2-bin \
        libffi-dev \
        libssl-dev \
        pkg-config \
        procps \
        python3 \
        python3-dev \
        python3-pip \
        python3-venv \
        ruby \
        ruby-dev \
        sudo \
        tini \
        wget \
    && rm -rf /var/lib/apt/lists/*

RUN if [ -f /etc/apt/sources.list.d/kali.sources ]; then \
        sed -i 's|http://http.kali.org/kali|https://kali.download/kali|g' /etc/apt/sources.list.d/kali.sources; \
    fi \
    && if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|http://http.kali.org/kali|https://kali.download/kali|g' /etc/apt/sources.list; \
    fi \
    && apt-get update -o Acquire::Retries=5 \
    && apt-get install -y -o Acquire::Retries=5 --fix-missing \
        kali-linux-default \
    && rm -rf /var/lib/apt/lists/*

RUN apt-get update -o Acquire::Retries=5 \
    && apt-get install -y -o Acquire::Retries=5 --fix-missing \
        adb \
        aircrack-ng \
        amass \
        arp-scan \
        arjun \
        awscli \
        azure-cli \
        bloodhound \
        bully \
        cewl \
        chisel \
        crackmapexec \
        crunch \
        default-mysql-client \
        dnsenum \
        dnsrecon \
        enum4linux \
        evil-winrm \
        exploitdb \
        feroxbuster \
        gsutil \
        hydra \
        jadx \
        kismet \
        kubectl \
        ldap-utils \
        medusa \
        mitmproxy \
        mongodb-clients \
        mongo-tools \
        mimikatz \
        netdiscover \
        netexec \
        nfs-common \
        nikto \
        onesixtyone \
        postgresql-client \
        powershell \
        python3-impacket \
        reaver \
        recon-ng \
        redis-tools \
        rubeus \
        sharphound \
        smbclient \
        smbmap \
        snmp \
        snmpcheck \
        theharvester \
        whatweb \
        wfuzz \
        wifite \
        yara \
        zbar-tools \
    && install -d -m 0755 /etc/apt/keyrings \
    && wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-linux.gpg \
    && chmod a+r /etc/apt/keyrings/google-linux.gpg \
    && if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        printf 'Types: deb\nURIs: https://dl.google.com/linux/chrome/deb/\nSuites: stable\nComponents: main\nArchitectures: amd64\nSigned-By: /etc/apt/keyrings/google-linux.gpg\n' > /etc/apt/sources.list.d/google-chrome.sources; \
       fi \
    && wget -qO- https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /etc/apt/keyrings/google-cloud.gpg \
    && chmod a+r /etc/apt/keyrings/google-cloud.gpg \
    && printf 'deb [signed-by=/etc/apt/keyrings/google-cloud.gpg] https://packages.cloud.google.com/apt cloud-sdk main\n' > /etc/apt/sources.list.d/google-cloud-cli.list \
    && apt-get update -o Acquire::Retries=5 \
    && apt-get install -y -o Acquire::Retries=5 --fix-missing google-cloud-cli \
    && if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
        apt-get install -y -o Acquire::Retries=5 --fix-missing google-chrome-stable; \
       else \
        printf '[aegissec-kali] Skip Google Chrome install on unsupported arch: %s\n' "$(dpkg --print-architecture)"; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/aegissec

COPY scripts/install_ctf_tools.sh ./scripts/install_ctf_tools.sh
COPY docker/kali/install_skill_tools.sh ./docker/kali/install_skill_tools.sh

RUN chmod +x ./scripts/install_ctf_tools.sh ./docker/kali/install_skill_tools.sh \
    && if [ "${INSTALL_CTF_TOOLS}" = "1" ]; then \
        ./scripts/install_ctf_tools.sh "${CTF_INSTALL_MODE}"; \
       else \
        echo "[aegissec-kali] Skip CTF baseline installation"; \
       fi \
    && if [ "${INSTALL_SKILL_TOOLS}" = "1" ]; then \
        ./docker/kali/install_skill_tools.sh "${SKILL_TOOL_PROFILE}"; \
       else \
        echo "[aegissec-kali] Skip skill extras installation"; \
       fi

RUN printf '%s\n' 'export PATH="/root/.local/bin:/root/go/bin:/root/.ctf-tools/venv/bin:/root/.aegissec-skill-tools/venv/bin:$PATH"' > /etc/profile.d/aegissec-path.sh \
    && chmod 0644 /etc/profile.d/aegissec-path.sh \
    && printf '\nexport PATH="/root/.local/bin:/root/go/bin:/root/.ctf-tools/venv/bin:/root/.aegissec-skill-tools/venv/bin:$PATH"\n' >> /etc/zsh/zshenv

WORKDIR /workspace

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/bin/zsh"]
