#!/usr/bin/env python3
"""
模式扫描脚本
Phase 2: 并行模式匹配
基于规则库定位潜在的Sink点和高风险代码模式
"""

import os
import re
import json
import argparse
from datetime import datetime

class PatternScanner:
    def __init__(self, target_dir, output_dir):
        self.target_dir = os.path.abspath(target_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.results = {
            'sql_injection': [],
            'command_injection': [],
            'xss': [],
            'path_traversal': [],
            'file_upload': [],
            'ssrf': [],
            'csrf': [],
            'xxe': [],
            'deserialization': [],
            'ssti': [],
            'rce': [],
            'idor': [],
            'auth_bypass': [],
            'sensitive_data': [],
            'hardcoded_credentials': []
        }
        
        os.makedirs(self.output_dir, exist_ok=True)
    
    def load_patterns(self):
        """加载漏洞检测模式"""
        patterns = {
            'sql_injection': [
                r'SELECT\s+.*FROM.*WHERE.*=.*["\']?\s*\+',
                r'INSERT\s+INTO.*VALUES\s*\(.*["\']?\s*\+',
                r'UPDATE\s+.*SET.*=.*["\']?\s*\+',
                r'DELETE\s+FROM.*WHERE.*=.*["\']?\s*\+',
                r'\$\w+\s*=\s*\$\w+',
                r'cursor\.execute\s*\(\s*["\']?\s*\+',
                r'query\s*=\s*["\']?\s*\+',
                r'execSQL\s*\(\s*["\']?\s*\+'
            ],
            'command_injection': [
                r'Runtime\.getRuntime\(\)\.exec\s*\(\s*["\']?\s*\+',
                r'ProcessBuilder\s*\(\s*\.command\s*\(\s*["\']?\s*\+',
                r'os\.system\s*\(\s*["\']?\s*\+',
                r'subprocess\.(run|call|Popen)\s*\(\s*["\']?\s*\+',
                r'exec\s*\(\s*["\']?\s*\+',
                r'shell_exec\s*\(\s*["\']?\s*\+',
                r'passthru\s*\(\s*["\']?\s*\+',
                r'popen\s*\(\s*["\']?\s*\+'
            ],
            'xss': [
                r'innerHTML\s*=\s*["\']?\s*\+',
                r'outerHTML\s*=\s*["\']?\s*\+',
                r'document\.write\s*\(\s*["\']?\s*\+',
                r'eval\s*\(\s*["\']?\s*\+',
                r'echo\s+\$\w+',
                r'print\s+\$\w+',
                r'render_template_string\s*\(\s*["\']?\s*\+',
                r'\$\w+\s*\+\s*request\.',
                r'response\.write\s*\(\s*["\']?\s*\+'
            ],
            'path_traversal': [
                r'File\s*\(\s*["\']?\s*\+',
                r'Files\.readAllBytes\s*\(\s*["\']?\s*\+',
                r'FileInputStream\s*\(\s*["\']?\s*\+',
                r'open\s*\(\s*["\']?\s*\+',
                r'\.\.\/\s*\.',
                r'\.\.\.',
                r'realpath\s*\(\s*["\']?\s*\+',
                r'getCanonicalPath\s*\(\s*["\']?\s*\+'
            ],
            'file_upload': [
                r'MultipartFile\s+\w+',
                r'FileItem\s+\w+',
                r'File\s*\(\s*["\']?\s*\+',
                r'Files\.write\s*\(\s*["\']?\s*\+',
                r'FileOutputStream\s*\(\s*["\']?\s*\+',
                r'getPart\s*\(\s*["\']?\s*\+',
                r'getSubmittedFileName\s*\(\s*["\']?\s*\+',
                r'getInputStream\s*\(\s*["\']?\s*\+'
            ],
            'ssrf': [
                r'URL\.openConnection\s*\(\s*["\']?\s*\+',
                r'HttpURLConnection\s+\w+',
                r'HttpClient\s+\w+',
                r'requests\.(get|post)\s*\(\s*["\']?\s*\+',
                r'urllib\.(request|urlopen)\s*\(\s*["\']?\s*\+',
                r'fetch\s*\(\s*["\']?\s*\+',
                r'axios\.(get|post)\s*\(\s*["\']?\s*\+'
            ],
            'csrf': [
                r'@RequestMapping\s*\(\s*["\']?\s*\+',
                r'@PostMapping\s*\(\s*["\']?\s*\+',
                r'@PutMapping\s*\(\s*["\']?\s*\+',
                r'@DeleteMapping\s*\(\s*["\']?\s*\+',
                r'form\s+method=["\']?post["\']?',
                r'\$_(GET|POST|REQUEST)',
                r'request\.getParameter',
                r'request\.params\[',
                r'request\.body\[',
                r'request\.query\['
            ],
            'xxe': [
                r'DocumentBuilder\s+\w+',
                r'XMLReader\s+\w+',
                r'SAXParser\s+\w+',
                r'parse\s*\(\s*["\']?\s*\+',
                r'XMLDecoder\s+\w+',
                r'XStream\s+\w+',
                r'Unmarshaller\s+\w+'
            ],
            'deserialization': [
                r'ObjectInputStream\s+\w+',
                r'readObject\s*\(\s*["\']?\s*\+',
                r'ObjectInputStream',
                r'XMLDecoder\s+\w+',
                r'XStream\s+\w+',
                r'pickle\.loads\s*\(\s*["\']?\s*\+',
                r'yaml\.load\s*\(\s*["\']?\s*\+',
                r'marshal\.load\s*\(\s*["\']?\s*\+',
                r'json\.loads\s*\(\s*["\']?\s*\+',
                r'unserialize\s*\(\s*["\']?\s*\+'
            ],
            'ssti': [
                r'SpEL\s+\w+',
                r'OGNL\s+\w+',
                r'Velocity\s+\w+',
                r'Thymeleaf\s+\w+',
                r'Jinja2\s+\w+',
                r'Mako\s+\w+',
                r'Django\.template\s+\w+',
                r'Smarty\s+\w+',
                r'Twig\s+\w+',
                r'\$\{.*\}',
                r'#\{.*\}',
                r'render_template_string\s*\(\s*["\']?\s*\+',
                r'Template\s*\(\s*["\']?\s*\+'
            ],
            'rce': [
                r'eval\s*\(\s*["\']?\s*\+',
                r'exec\s*\(\s*["\']?\s*\+',
                r'Function\s*\(\s*["\']?\s*\+',
                r'assert\s*\(\s*["\']?\s*\+',
                r'create_function\s*\(\s*["\']?\s*\+',
                r'call_user_func\s*\(\s*["\']?\s*\+',
                r'call_user_func_array\s*\(\s*["\']?\s*\+',
                r'preg_replace\s*\([^)]*/e',
                r'system\s*\(\s*["\']?\s*\+',
                r'shell_exec\s*\(\s*["\']?\s*\+'
            ],
            'idor': [
                r'WHERE\s+.*user_id\s*=',
                r'WHERE\s+.*id\s*=\s*["\']?\s*\+',
                r'SELECT\s+.*FROM.*\$\w+',
                r'UPDATE\s+.*SET\s+.*\$\w+',
                r'DELETE\s+.*WHERE\s+.*\$\w+',
                r'getById\s*\(\s*["\']?\s*\+',
                r'findById\s*\(\s*["\']?\s*\+',
                r'request\.getParameter\(["\']?id["\']?\)',
                r'request\.params\["\']?id["\']?\]'
            ],
            'auth_bypass': [
                r'if\s*\(\s*password\s*==\s*["\']?\s*\+',
                r'if\s*\(\s*strcmp\s*\(\s*password\s*,\s*["\']?\s*\+',
                r'if\s*\(\s*authenticated\s*==\s*true\s*\)\s*\{\s*',
                r'if\s*\(\s*!authenticated\s*\)\s*\{\s*',
                r'excludePathPatterns\s*\(\s*["\']?\s*\+',
                r'permitAll\s*\(\s*["\']?\s*\+',
                r'@PreAuthorize\s*\(\s*["\']?\s*\+',
                r'@RolesAllowed\s*\(\s*["\']?\s*\+',
                r'login\s*\(\s*["\']?\s*\+',
                r'authenticate\s*\(\s*["\']?\s*\+'
            ],
            'sensitive_data': [
                r'password\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'secret\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'key\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'token\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'api_key\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'apikey\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'credential\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'database\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'jdbc:mysql://[^"\']+:[^"\']+@',
                r'jdbc:postgresql://[^"\']+:[^"\']+@',
                r'mongodb://[^"\']+:[^"\']+@',
                r'return\s+\$\w+',
                r'print\s+\$\w+',
                r'echo\s+\$\w+',
                r'logger\.(info|debug|error)\s*\(\s*["\']?\s*\+',
                r'console\.log\s*\(\s*["\']?\s*\+'
            ],
            'hardcoded_credentials': [
                r'["\']?password["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?secret["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?key["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?token["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?api_key["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?apikey["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?credential["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?username["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?admin["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'["\']?root["\']?\s*=\s*["\']?\s*["\']?\s*["\']?\s*',
                r'const\s+PASSWORD\s*=',
                r'const\s+SECRET\s*=',
                r'const\s+KEY\s*=',
                r'const\s+TOKEN\s*=',
                r'const\s+API_KEY\s*=',
                r'const\s+CREDENTIAL\s*=',
                r'private\s+static\s+final\s+String\s+PASSWORD\s*=',
                r'private\s+static\s+final\s+String\s+SECRET\s*=',
                r'private\s+static\s+final\s+String\s+KEY\s*=',
                r'private\s+static\s+final\s+String\s+TOKEN\s*=',
                r'private\s+static\s+final\s+String\s+API_KEY\s*=',
                r'private\s+static\s+final\s+String\s+CREDENTIAL\s*='
            ]
        }
        
        return patterns
    
    def scan_file(self, file_path, patterns):
        """扫描单个文件"""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                
                for vuln_type, pattern_list in patterns.items():
                    for pattern in pattern_list:
                        matches = re.finditer(pattern, content, re.IGNORECASE)
                        for match in matches:
                            line_number = content[:match.start()].count('\n') + 1
                            line_content = content.split('\n')[line_number - 1].strip()
                            
                            self.results[vuln_type].append({
                                'file': file_path,
                                'line_number': line_number,
                                'pattern': pattern,
                                'line_content': line_content[:100]
                            })
        except Exception as e:
            print(f"[-] 扫描文件 {file_path} 出错: {str(e)}")
    
    def scan_directory(self, patterns):
        """扫描目录"""
        print("[*] 开始模式匹配扫描...")
        
        ignore_dirs = ['node_modules', 'vendor', 'target', 'dist', 'build', '.git', '__pycache__', 'venv', 'env']
        
        total_files = 0
        scanned_files = 0
        
        for root, dirs, files in os.walk(self.target_dir):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            
            for file in files:
                if not file.endswith(('.php', '.py', '.js', '.java', '.go', '.jsp', '.html', '.xml', '.json', '.yaml', '.yml', '.rb', '.pl')):
                    continue
                
                total_files += 1
                file_path = os.path.join(root, file)
                
                self.scan_file(file_path, patterns)
                scanned_files += 1
        
        print(f"[+] 扫描完成: {scanned_files}/{total_files} 个文件")
    
    def generate_report(self):
        """生成扫描报告"""
        print("[*] 生成模式匹配报告...")
        
        report = {
            'scan_date': datetime.now().isoformat(),
            'target_directory': self.target_dir,
            'scan_results': self.results,
            'summary': {
                'sql_injection': len(self.results['sql_injection']),
                'command_injection': len(self.results['command_injection']),
                'xss': len(self.results['xss']),
                'path_traversal': len(self.results['path_traversal']),
                'file_upload': len(self.results['file_upload']),
                'ssrf': len(self.results['ssrf']),
                'csrf': len(self.results['csrf']),
                'xxe': len(self.results['xxe']),
                'deserialization': len(self.results['deserialization']),
                'ssti': len(self.results['ssti']),
                'rce': len(self.results['rce']),
                'idor': len(self.results['idor']),
                'auth_bypass': len(self.results['auth_bypass']),
                'sensitive_data': len(self.results['sensitive_data']),
                'hardcoded_credentials': len(self.results['hardcoded_credentials'])
            }
        }
        
        output_file = os.path.join(self.output_dir, 'pattern-scan-report.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"[+] 模式匹配报告生成完成: {output_file}")
        print(f"    - SQL注入: {report['summary']['sql_injection']}")
        print(f"    - 命令注入: {report['summary']['command_injection']}")
        print(f"    - XSS: {report['summary']['xss']}")
        print(f"    - 路径遍历: {report['summary']['path_traversal']}")
        print(f"    - 文件上传: {report['summary']['file_upload']}")
        print(f"    - SSRF: {report['summary']['ssrf']}")
        print(f"    - CSRF: {report['summary']['csrf']}")
        print(f"    - XXE: {report['summary']['xxe']}")
        print(f"    - 反序列化: {report['summary']['deserialization']}")
        print(f"    - SSTI: {report['summary']['ssti']}")
        print(f"    - RCE: {report['summary']['rce']}")
        print(f"    - IDOR: {report['summary']['idor']}")
        print(f"    - 认证绕过: {report['summary']['auth_bypass']}")
        print(f"    - 敏感数据: {report['summary']['sensitive_data']}")
        print(f"    - 硬编码凭证: {report['summary']['hardcoded_credentials']}")
    
    def run(self):
        """运行模式匹配扫描"""
        print(f"[*] 开始模式匹配扫描: {self.target_dir}")
        print(f"[*] 扫描结果将保存至: {self.output_dir}")
        print("=" * 80)
        
        patterns = self.load_patterns()
        self.scan_directory(patterns)
        
        print("=" * 80)
        self.generate_report()
        print("[*] 模式匹配扫描完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="模式匹配扫描工具")
    parser.add_argument('target', help='目标目录')
    parser.add_argument('-o', '--output', default='pattern-scan-results', help='输出目录')
    
    args = parser.parse_args()
    
    scanner = PatternScanner(args.target, args.output)
    scanner.run()