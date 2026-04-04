#!/usr/bin/env python3
"""
敏感信息检测脚本
Phase 2: 检测硬编码凭证和敏感信息
"""

import os
import re
import json
import argparse
from datetime import datetime

class SecretFinder:
    def __init__(self, target_dir, output_dir):
        self.target_dir = os.path.abspath(target_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.results = {
            'passwords': [],
            'api_keys': [],
            'tokens': [],
            'database_credentials': [],
            'secret_keys': [],
            'aws_credentials': [],
            'other_secrets': []
        }
        
        os.makedirs(self.output_dir, exist_ok=True)
    
    def scan_file(self, file_path):
        """扫描单个文件"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
                password_patterns = [
                    r'password\s*=\s*["\']([^"\']{8,})["\']',
                    r'pwd\s*=\s*["\']([^"\']{8,})["\']',
                    r'passwd\s*=\s*["\']([^"\']{8,})["\']',
                    r'["\']?password["\']?\s*=\s*["\']([^"\']{8,})["\']',
                    r'["\']?pwd["\']?\s*=\s*["\']([^"\']{8,})["\']',
                    r'PASSWORD\s*=\s*["\']([^"\']{8,})["\']',
                    r'PWD\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+PASSWORD\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+PWD\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+PASSWORD\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+PWD\s*=\s*["\']([^"\']{8,})["\']'
                ]
                
                api_key_patterns = [
                    r'api[_-]?key\s*=\s*["\']([^"\']{8,})["\']',
                    r'apikey\s*=\s*["\']([^"\']{8,})["\']',
                    r'api[_-]?secret\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+API_KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+APIKEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+API_SECRET\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+APIKEY\s*=\s*["\']([^"\']{8,})["\']'
                ]
                
                token_patterns = [
                    r'token\s*=\s*["\']([^"\']{8,})["\']',
                    r'jwt\s*=\s*["\']([^"\']{8,})["\']',
                    r'auth[_-]?token\s*=\s*["\']([^"\']{8,})["\']',
                    r'access[_-]?token\s*=\s*["\']([^"\']{8,})["\']',
                    r'refresh[_-]?token\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+TOKEN\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+JWT\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+TOKEN\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+JWT\s*=\s*["\']([^"\']{8,})["\']',
                    r'Bearer\s+["\']([^"\']{20,})["\']',
                    r'Authorization:\s+Bearer\s+["\']([^"\']{20,})["\']'
                ]
                
                secret_key_patterns = [
                    r'secret[_-]?key\s*=\s*["\']([^"\']{8,})["\']',
                    r'secret\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+SECRET[_-]?KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+SECRET\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+SECRET[_-]?KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+SECRET\s*=\s*["\']([^"\']{8,})["\']',
                    r'APP_SECRET\s*=\s*["\']([^"\']{8,})["\']',
                    r'JWT_SECRET\s*=\s*["\']([^"\']{8,})["\']',
                    r'ENCRYPTION_KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+SECRET_KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+APP_SECRET\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+JWT_SECRET\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+ENCRYPTION_KEY\s*=\s*["\']([^"\']{8,})["\']'
                ]
                
                database_patterns = [
                    r'jdbc:[^"\']+:[^"\']+@',
                    r'mongodb://[^"\']+:[^"\']+@',
                    r'mysql://[^"\']+:[^"\']+@',
                    r'postgresql://[^"\']+:[^"\']+@',
                    r'redis://[^"\']+:[^"\']+@',
                    r'DB_HOST\s*=\s*["\']([^"\']{8,})["\']',
                    r'DB_PORT\s*=\s*["\']([^"\']{8,})["\']',
                    r'DB_USER\s*=\s*["\']([^"\']{8,})["\']',
                    r'DB_PASS\s*=\s*["\']([^"\']{8,})["\']',
                    r'DATABASE_HOST\s*=\s*["\']([^"\']{8,})["\']',
                    r'DATABASE_PORT\s*=\s*["\']([^"\']{8,})["\']',
                    r'DATABASE_USER\s*=\s*["\']([^"\']{8,})["\']',
                    r'DATABASE_PASSWORD\s*=\s*["\']([^"\']{8,})["\']',
                    r'DB_PASSWORD\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DB_HOST\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DB_PORT\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DB_USER\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DB_PASSWORD\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DATABASE_HOST\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DATABASE_PORT\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DATABASE_USER\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+DATABASE_PASSWORD\s*=\s*["\']([^"\']{8,})["\']'
                ]
                
                aws_patterns = [
                    r'aws_access_key_id\s*=\s*["\']([^"\']{8,})["\']',
                    r'aws_secret_access_key\s*=\s*["\']([^"\']{8,})["\']',
                    r'aws_session_token\s*=\s*["\']([^"\']{8,})["\']',
                    r'AWS_ACCESS_KEY_ID\s*=\s*["\']([^"\']{8,})["\']',
                    r'AWS_SECRET_ACCESS_KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'AWS_SESSION_TOKEN\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+AWS_ACCESS_KEY_ID\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+AWS_SECRET_ACCESS_KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+AWS_SESSION_TOKEN\s*=\s*["\']([^"\']{8,})["\']',
                    r'aws_access_key_id\s*=\s*["\']([^"\']{8,})["\']',
                    r'aws_secret_access_key\s*=\s*["\']([^"\']{8,})["\']',
                    r'aws_session_token\s*=\s*["\']([^"\']{8,})["\']',
                    r'AWS_ACCESS_KEY_ID\s*=\s*["\']([^"\']{8,})["\']',
                    r'AWS_SECRET_ACCESS_KEY\s*=\s*["\']([^"\']{8,})["\']',
                    r'AWS_SESSION_TOKEN\s*=\s*["\']([^"\']{8,})["\']'
                ]
                
                other_patterns = [
                    r'credential\s*=\s*["\']([^"\']{8,})["\']',
                    r'username\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+USERNAME\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+CREDENTIAL\s*=\s*["\']([^"\']{8,})["\']',
                    r'admin\s*=\s*["\']([^"\']{8,})["\']',
                    r'root\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+ADMIN\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+ROOT\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+ADMIN\s*=\s*["\']([^"\']{8,})["\']',
                    r'private\s+static\s+final\s+String\s+ROOT\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+ADMIN\s*=\s*["\']([^"\']{8,})["\']',
                    r'const\s+ROOT\s*=\s*["\']([^"\']{8,})["\']'
                ]
                
                all_patterns = {
                    'passwords': password_patterns,
                    'api_keys': api_key_patterns,
                    'tokens': token_patterns,
                    'database_credentials': database_patterns,
                    'secret_keys': secret_key_patterns,
                    'aws_credentials': aws_patterns,
                    'other_secrets': other_patterns
                }
                
                for category, patterns in all_patterns.items():
                    for pattern in patterns:
                        matches = re.finditer(pattern, content, re.IGNORECASE)
                        for match in matches:
                            line_number = content[:match.start()].count('\n') + 1
                            line_content = content.split('\n')[line_number - 1].strip()
                            
                            value_match = re.search(r'["\']([^"\']{8,})["\']', line_content)
                            value = value_match.group(1) if value_match else 'N/A'
                            
                            self.results[category].append({
                                'file': file_path,
                                'line_number': line_number,
                                'pattern': pattern,
                                'line_content': line_content[:100],
                                'value': value[:50]
                            })
                            
        except Exception as e:
            pass
    
    def scan_directory(self):
        """扫描目录"""
        print("[*] 开始敏感信息扫描...")
        
        ignore_dirs = ['node_modules', 'vendor', 'target', 'dist', 'build', '.git', '__pycache__', 'venv', 'env', 'venv', 'env', '.env']
        ignore_files = ['package-lock.json', 'yarn.lock', 'composer.lock', 'requirements.lock', 'poetry.lock', 'Gemfile.lock', 'go.sum', 'Cargo.lock']
        
        total_files = 0
        scanned_files = 0
        
        for root, dirs, files in os.walk(self.target_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            
            for file in files:
                if file in ignore_files:
                    continue
                
                if not file.endswith(('.php', '.py', '.js', '.java', '.go', '.jsp', '.html', '.xml', '.json', '.yaml', '.yml', '.rb', '.pl', '.sh', '.ini', '.conf', '.env', '.properties', '.gradle', '.kt', '.ts', '.tsx')):
                    continue
                
                total_files += 1
                file_path = os.path.join(root, file)
                
                self.scan_file(file_path)
                scanned_files += 1
        
        print(f"[+] 扫描完成: {scanned_files}/{total_files} 个文件")
        print(f"    - 密码: {len(self.results['passwords'])}")
        print(f"    - API密钥: {len(self.results['api_keys'])}")
        print(f"    - Token: {len(self.results['tokens'])}")
        print(f"    - 数据库凭证: {len(self.results['database_credentials'])}")
        print(f"    - 密钥: {len(self.results['secret_keys'])}")
        print(f"    - AWS凭证: {len(self.results['aws_credentials'])}")
        print(f"    - 其他敏感信息: {len(self.results['other_secrets'])}")
    
    def generate_report(self):
        """生成扫描报告"""
        print("[*] 生成敏感信息扫描报告...")
        
        report = {
            'scan_date': datetime.now().isoformat(),
            'target_directory': self.target_dir,
            'scan_results': self.results,
            'summary': {
                'passwords': len(self.results['passwords']),
                'api_keys': len(self.results['api_keys']),
                'tokens': len(self.results['tokens']),
                'database_credentials': len(self.results['database_credentials']),
                'secret_keys': len(self.results['secret_keys']),
                'aws_credentials': len(self.results['aws_credentials']),
                'other_secrets': len(self.results['other_secrets']),
                'total_secrets': sum([len(v) for v in self.results.values()])
            }
        }
        
        output_file = os.path.join(self.output_dir, 'secret-scan-report.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"[+] 敏感信息扫描报告生成完成: {output_file}")
        print(f"    - 总敏感信息数: {report['summary']['total_secrets']}")
    
    def run(self):
        """运行敏感信息扫描"""
        print(f"[*] 开始敏感信息扫描: {self.target_dir}")
        print(f"[*] 扫描结果将保存至: {self.output_dir}")
        print("=" * 80)
        
        self.scan_directory()
        
        print("=" * 80)
        self.generate_report()
        print("[*] 敏感信息扫描完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="敏感信息检测工具")
    parser.add_argument('target', help='目标目录')
    parser.add_argument('-o', '--output', default='secret-scan-results', help='输出目录')
    
    args = parser.parse_args()
    
    finder = SecretFinder(args.target, args.output)
    finder.run()