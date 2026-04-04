# Docker 部署验证

> 对于深度审计，可使用 Docker 沙箱进行**动态验证**
> 版本: 1.0.0

## 概述

Docker 部署验证是 Phase 4 的可选任务，目的是通过动态验证确认漏洞的可利用性。

## 使用方法

### 生成验证环境
```bash
# 生成验证环境
code-audit --generate-docker-env
```

### 启动并验证
```bash
# 启动 Docker 容器
docker-compose up -d

# 执行验证脚本
docker exec -it sandbox python /workspace/poc/verify_all.py
```

## 验证脚本

### verify_all.py
```python
#!/usr/bin/env python3
"""
验证所有发现的漏洞
"""
import requests
import sys

def verify_vulnerability(vuln_id, url, payload):
    """验证单个漏洞"""
    try:
        response = requests.post(url, data=payload, timeout=10)
        if response.status_code == 200:
            print(f"[✓] {vuln_id}: 漏洞可利用")
            return True
        else:
            print(f"[✗] {vuln_id}: 漏洞不可利用 (HTTP {response.status_code})")
            return False
    except Exception as e:
        print(f"[✗] {vuln_id}: 验证失败 - {str(e)}")
        return False

if __name__ == "__main__":
    # 从配置文件读取漏洞列表
    vulnerabilities = load_vulnerabilities("vulnerabilities.json")
    
    results = []
    for vuln in vulnerabilities:
        result = verify_vulnerability(
            vuln['id'],
            vuln['url'],
            vuln['payload']
        )
        results.append(result)
    
    # 输出验证结果
    print(f"\n验证结果: {sum(results)}/{len(results)} 个漏洞可利用")
```

## 注意事项

1. **安全第一**: 验证环境必须与生产环境隔离
2. **授权验证**: 确保有授权才能进行验证
3. **数据保护**: 验证数据不得包含真实敏感信息
4. **日志记录**: 记录所有验证操作
5. **环境清理**: 验证完成后清理环境
