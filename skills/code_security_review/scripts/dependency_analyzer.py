#!/usr/bin/env python3
"""
依赖分析脚本
Phase 1/2: 分析项目依赖和第三方库
识别已知漏洞和版本问题
"""

import os
import re
import json
import argparse
from datetime import datetime
import subprocess

class DependencyAnalyzer:
    def __init__(self, target_dir, output_dir):
        self.target_dir = os.path.abspath(target_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.results = {
            'dependencies': [],
            'vulnerabilities': [],
            'outdated_packages': [],
            'missing_lock_files': []
        }
        
        os.makedirs(self.output_dir, exist_ok=True)
    
    def detect_package_managers(self):
        """检测项目使用的包管理器"""
        print("[*] 检测包管理器...")
        
        package_managers = {
            'package.json': 'npm',
            'package-lock.json': 'npm',
            'yarn.lock': 'yarn',
            'pnpm-lock.yaml': 'pnpm',
            'requirements.txt': 'pip',
            'Pipfile': 'pip',
            'poetry.lock': 'poetry',
            'setup.py': 'setuptools',
            'pyproject.toml': 'poetry',
            'pom.xml': 'maven',
            'build.gradle': 'gradle',
            'build.gradle.kts': 'gradle',
            'composer.json': 'composer',
            'composer.lock': 'composer',
            'Gemfile': 'bundler',
            'Gemfile.lock': 'bundler',
            'go.mod': 'go',
            'go.sum': 'go',
            'Cargo.toml': 'cargo',
            'Cargo.lock': 'cargo'
        }
        
        found_managers = []
        for root, dirs, files in os.walk(self.target_dir):
            for file in files:
                if file in package_managers:
                    manager = package_managers[file]
                    if manager not in found_managers:
                        found_managers.append(manager)
                        file_path = os.path.join(root, file)
                        self.results['dependencies'].append({
                            'manager': manager,
                            'file': file_path,
                            'path': os.path.relpath(file_path, self.target_dir)
                        })
        
        print(f"[+] 发现包管理器: {', '.join(found_managers)}")
        return found_managers
    
    def analyze_dependencies(self, package_manager):
        """分析依赖项"""
        print(f"[*] 分析 {package_manager} 依赖...")
        
        if package_manager == 'npm':
            self._analyze_npm_dependencies()
        elif package_manager == 'pip':
            self._analyze_pip_dependencies()
        elif package_manager == 'maven':
            self._analyze_maven_dependencies()
        elif package_manager == 'composer':
            self._analyze_composer_dependencies()
        elif package_manager == 'gradle':
            self._analyze_gradle_dependencies()
        elif package_manager == 'go':
            self._analyze_go_dependencies()
        elif package_manager == 'cargo':
            self._analyze_cargo_dependencies()
        elif package_manager == 'bundler':
            self._analyze_bundler_dependencies()
    
    def _analyze_npm_dependencies(self):
        """分析npm依赖"""
        package_json = os.path.join(self.target_dir, 'package.json')
        if not os.path.exists(package_json):
            return
        
        try:
            with open(package_json, 'r', encoding='utf-8') as f:
                package_data = json.load(f)
            
            dependencies = package_data.get('dependencies', {})
            dev_dependencies = package_data.get('devDependencies', {})
            
            all_deps = {**dependencies, **dev_dependencies}
            
            for dep_name, dep_version in all_deps.items():
                self.results['dependencies'].append({
                    'manager': 'npm',
                    'name': dep_name,
                    'version': dep_version,
                    'type': 'production' if dep_name in dependencies else 'development'
                })
                
            print(f"[+] 发现 {len(all_deps)} 个npm依赖")
            
            self._check_missing_lock_file('package-lock.json')
            
        except Exception as e:
            print(f"[-] 分析npm依赖出错: {str(e)}")
    
    def _analyze_pip_dependencies(self):
        """分析pip依赖"""
        requirements_txt = os.path.join(self.target_dir, 'requirements.txt')
        if not os.path.exists(requirements_txt):
            return
        
        try:
            with open(requirements_txt, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                match = re.match(r'^([a-zA-Z0-9_-]+)\s*([>=<~=!]+)?\s*([^\s#]+)?', line)
                if match:
                    dep_name = match.group(1)
                    dep_version = match.group(3) if match.group(3) else 'any'
                    
                    self.results['dependencies'].append({
                        'manager': 'pip',
                        'name': dep_name,
                        'version': dep_version
                    })
            
            print(f"[+] 发现 {len([l for l in lines if l.strip() and not l.startswith('#')])} 个pip依赖")
            
            self._check_missing_lock_file('requirements.lock' if os.path.exists(os.path.join(self.target_dir, 'Pipfile.lock')) else 'poetry.lock')
            
        except Exception as e:
            print(f"[-] 分析pip依赖出错: {str(e)}")
    
    def _analyze_maven_dependencies(self):
        """分析maven依赖"""
        pom_xml = os.path.join(self.target_dir, 'pom.xml')
        if not os.path.exists(pom_xml):
            return
        
        try:
            with open(pom_xml, 'r', encoding='utf-8') as f:
                content = f.read()
            
            dependency_pattern = r'<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>\s*<version>([^<]+)</version>'
            matches = re.findall(dependency_pattern, content)
            
            for match in matches:
                group_id, artifact_id, version = match
                self.results['dependencies'].append({
                    'manager': 'maven',
                    'group_id': group_id,
                    'artifact_id': artifact_id,
                    'version': version
                })
            
            print(f"[+] 发现 {len(matches)} 个maven依赖")
            
        except Exception as e:
            print(f"[-] 分析maven依赖出错: {str(e)}")
    
    def _analyze_composer_dependencies(self):
        """分析composer依赖"""
        composer_json = os.path.join(self.target_dir, 'composer.json')
        if not os.path.exists(composer_json):
            return
        
        try:
            with open(composer_json, 'r', encoding='utf-8') as f:
                package_data = json.load(f)
            
            require = package_data.get('require', {})
            require_dev = package_data.get('require-dev', {})
            
            all_deps = {**require, **require_dev}
            
            for dep_name, dep_version in all_deps.items():
                self.results['dependencies'].append({
                    'manager': 'composer',
                    'name': dep_name,
                    'version': dep_version,
                    'type': 'production' if dep_name in require else 'development'
                })
            
            print(f"[+] 发现 {len(all_deps)} 个composer依赖")
            
            self._check_missing_lock_file('composer.lock')
            
        except Exception as e:
            print(f"[-] 分析composer依赖出错: {str(e)}")
    
    def _analyze_gradle_dependencies(self):
        """分析gradle依赖"""
        gradle_files = ['build.gradle', 'build.gradle.kts']
        
        for gradle_file in gradle_files:
            file_path = os.path.join(self.target_dir, gradle_file)
            if not os.path.exists(file_path):
                continue
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                implementation_pattern = r'implementation\s+[\'"]([^\'"]+)[\'"]'
                matches = re.findall(implementation_pattern, content)
                
                for match in matches:
                    self.results['dependencies'].append({
                        'manager': 'gradle',
                        'name': match,
                        'version': 'unknown'
                    })
                
                print(f"[+] 发现 {len(matches)} 个gradle依赖")
                
            except Exception as e:
                print(f"[-] 分析gradle依赖出错: {str(e)}")
    
    def _analyze_go_dependencies(self):
        """分析go依赖"""
        go_mod = os.path.join(self.target_dir, 'go.mod')
        if not os.path.exists(go_mod):
            return
        
        try:
            with open(go_mod, 'r', encoding='utf-8') as f:
                content = f.read()
            
            require_pattern = r'require\s+([^\s]+)\s+v([^\s]+)'
            matches = re.findall(require_pattern, content)
            
            for match in matches:
                self.results['dependencies'].append({
                    'manager': 'go',
                    'name': match[0],
                    'version': match[1]
                })
            
            print(f"[+] 发现 {len(matches)} 个go依赖")
            
        except Exception as e:
            print(f"[-] 分析go依赖出错: {str(e)}")
    
    def _analyze_cargo_dependencies(self):
        """分析cargo依赖"""
        cargo_toml = os.path.join(self.target_dir, 'Cargo.toml')
        if not os.path.exists(cargo_toml):
            return
        
        try:
            with open(cargo_toml, 'r', encoding='utf-8') as f:
                content = f.read()
            
            dependency_pattern = r'^([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"'
            matches = re.findall(dependency_pattern, content, re.MULTILINE)
            
            for match in matches:
                self.results['dependencies'].append({
                    'manager': 'cargo',
                    'name': match[0],
                    'version': match[1]
                })
            
            print(f"[+] 发现 {len(matches)} 个cargo依赖")
            
        except Exception as e:
            print(f"[-] 分析cargo依赖出错: {str(e)}")
    
    def _analyze_bundler_dependencies(self):
        """分析bundler依赖"""
        gemfile = os.path.join(self.target_dir, 'Gemfile')
        if not os.path.exists(gemfile):
            return
        
        try:
            with open(gemfile, 'r', encoding='utf-8') as f:
                content = f.read()
            
            gem_pattern = r'gem\s+[\'"]([^\'"]+)[\'"]'
            matches = re.findall(gem_pattern, content)
            
            for match in matches:
                self.results['dependencies'].append({
                    'manager': 'bundler',
                    'name': match,
                    'version': 'unknown'
                })
            
            print(f"[+] 发现 {len(matches)} 个bundler依赖")
            
        except Exception as e:
            print(f"[-] 分析bundler依赖出错: {str(e)}")
    
    def _check_missing_lock_file(self, lock_file):
        """检查缺失的lock文件"""
        lock_file_path = os.path.join(self.target_dir, lock_file)
        if not os.path.exists(lock_file_path):
            self.results['missing_lock_files'].append({
                'manager': lock_file.split('.')[0],
                'lock_file': lock_file
            })
            print(f"[!] 缺失lock文件: {lock_file}")
    
    def run_security_audit(self):
        """运行安全审计"""
        print("[*] 运行依赖安全审计...")
        
        try:
            if 'npm' in [dep['manager'] for dep in self.results['dependencies']]:
                result = subprocess.run(
                    ['npm', 'audit', '--json'],
                    cwd=self.target_dir,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode == 0 or result.returncode == 1:
                    try:
                        audit_results = json.loads(result.stdout)
                        vulnerabilities = audit_results.get('vulnerabilities', {})
                        
                        for vuln_id, vuln_info in vulnerabilities.items():
                            self.results['vulnerabilities'].append({
                                'manager': 'npm',
                                'vulnerability_id': vuln_id,
                                'package': vuln_info.get('name', ''),
                                'severity': vuln_info.get('severity', 'unknown'),
                                'title': vuln_info.get('title', ''),
                                'url': vuln_info.get('url', '')
                            })
                        
                        print(f"[+] 发现 {len(vulnerabilities)} 个npm依赖漏洞")
                    except:
                        pass
            
            if 'pip' in [dep['manager'] for dep in self.results['dependencies']]:
                result = subprocess.run(
                    ['pip', 'audit', '--format', 'json'],
                    cwd=self.target_dir,
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                
                if result.returncode == 0 or result.returncode == 1:
                    try:
                        audit_results = json.loads(result.stdout)
                        vulnerabilities = audit_results.get('vulnerabilities', {})
                        
                        for vuln_id, vuln_info in vulnerabilities.items():
                            self.results['vulnerabilities'].append({
                                'manager': 'pip',
                                'vulnerability_id': vuln_id,
                                'package': vuln_info.get('name', ''),
                                'severity': vuln_info.get('severity', 'unknown'),
                                'title': vuln_info.get('title', ''),
                                'url': vuln_info.get('url', '')
                            })
                        
                        print(f"[+] 发现 {len(vulnerabilities)} 个pip依赖漏洞")
                    except:
                        pass
        
        except Exception as e:
            print(f"[-] 安全审计出错: {str(e)}")
    
    def generate_report(self):
        """生成分析报告"""
        print("[*] 生成依赖分析报告...")
        
        report = {
            'scan_date': datetime.now().isoformat(),
            'target_directory': self.target_dir,
            'scan_results': self.results,
            'summary': {
                'total_dependencies': len(self.results['dependencies']),
                'total_vulnerabilities': len(self.results['vulnerabilities']),
                'missing_lock_files': len(self.results['missing_lock_files']),
                'package_managers': list(set([dep['manager'] for dep in self.results['dependencies']]))
            }
        }
        
        output_file = os.path.join(self.output_dir, 'dependency-analysis-report.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        print(f"[+] 依赖分析报告生成完成: {output_file}")
        print(f"    - 总依赖数: {report['summary']['total_dependencies']}")
        print(f"    - 漏洞数: {report['summary']['total_vulnerabilities']}")
        print(f"    - 缺失lock文件: {report['summary']['missing_lock_files']}")
    
    def run(self):
        """运行依赖分析"""
        print(f"[*] 开始依赖分析: {self.target_dir}")
        print(f"[*] 分析结果将保存至: {self.output_dir}")
        print("=" * 80)
        
        package_managers = self.detect_package_managers()
        
        for manager in package_managers:
            self.analyze_dependencies(manager)
        
        self.run_security_audit()
        
        print("=" * 80)
        self.generate_report()
        print("[*] 依赖分析完成！")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="依赖分析工具")
    parser.add_argument('target', help='目标目录')
    parser.add_argument('-o', '--output', default='dependency-analysis-results', help='输出目录')
    
    args = parser.parse_args()
    
    analyzer = DependencyAnalyzer(args.target, args.output)
    analyzer.run()