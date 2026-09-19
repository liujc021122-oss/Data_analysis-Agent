# -*- coding: utf-8 -*-
"""
安全的代码执行器，基于 IPython 提供 notebook 环境下的代码执行功能
"""

import os
import sys
import ast
import builtins
import traceback
import io
from typing import Dict, Any, Iterable, List, Optional, Tuple
from contextlib import redirect_stdout, redirect_stderr
from IPython.core.interactiveshell import InteractiveShell
from IPython.utils.capture import capture_output
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import pandas as pd

class CodeExecutor:
    """
    安全的代码执行器，限制依赖库，捕获输出，支持图片保存与路径输出
    """
    ALLOWED_IMPORTS = {
        'pandas', 'pd',
        'numpy', 'np',
        'matplotlib', 'matplotlib.pyplot', 'plt',
        'duckdb', 'scipy', 'sklearn',
        'plotly', 'dash', 'requests', 'urllib',
        'os', 'sys', 'json', 'csv', 'datetime', 'time',
        'math', 'statistics', 're', 'pathlib', 'io',
        'collections', 'itertools', 'functools', 'operator',
        'warnings', 'logging', 'copy', 'pickle', 'gzip', 'zipfile',
        'typing', 'dataclasses', 'enum', 'sqlite3'
    }

    def __init__(self, output_dir: str = "outputs"):
        """
        初始化代码执行器

        Args:
            output_dir: 输出目录，用于保存图片和文件
        """
        self.output_dir = os.path.abspath(output_dir)
        os.makedirs(self.output_dir, exist_ok=True)

        # 初始化 IPython shell
        self.shell = InteractiveShell()
        self.sensitive_columns: set[str] = set()
        self._sensitive_values: set[str] = set()
        for table_type in (pd.DataFrame, pd.Series):
            self.shell.display_formatter.formatters['text/plain'].for_type(
                table_type, self._display_table
            )
        self.shell.display_formatter.formatters['text/html'].for_type(
            pd.DataFrame, lambda frame: self._redact_table(frame)._repr_html_()
        )

        # 设置中文字体
        self._setup_chinese_font()

        # 预导入常用库
        self._setup_common_imports()

        # 图片计数器
        self.image_counter = 0

    def _setup_chinese_font(self):
        """设置matplotlib中文字体显示"""
        try:
            # 设置matplotlib使用Agg backend避免GUI问题
            matplotlib.use('Agg')

            # 设置matplotlib使用simhei字体显示中文
            plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial Unicode MS']
            plt.rcParams['axes.unicode_minus'] = False
              # 在shell中也设置
            self.shell.run_cell("""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans', 'Arial Unicode MS']
plt.rcParams['axes.unicode_minus'] = False
""")
        except Exception as e:
            print(f"设置中文字体失败: {e}")

    def _setup_common_imports(self):
        """预导入常用库"""
        common_imports = """
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import duckdb
import os
import json
from IPython.display import display
"""
        try:
            self.shell.run_cell(common_imports)
            # 确保display函数在shell的用户命名空间中可用
            self.shell.user_ns['display'] = self._display_redacted
            self.shell.user_ns['print'] = self._print_redacted
        except Exception as e:
            print(f"预导入库失败: {e}")

    def _check_code_safety(self, code: str) -> Tuple[bool, str]:
        """
        检查代码安全性，限制导入的库

        Returns:
            (is_safe, error_message)
        """
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"语法错误: {e}"

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name not in self.ALLOWED_IMPORTS:
                        return False, f"不允许的导入: {alias.name}"

            elif isinstance(node, ast.ImportFrom):
                if node.module not in self.ALLOWED_IMPORTS:
                    return False, f"不允许的导入: {node.module}"

            # 检查危险函数调用
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    if node.func.id in ['exec', 'eval', 'open', '__import__']:
                        return False, f"不允许的函数调用: {node.func.id}"

        return True, ""

    def get_current_figures_info(self) -> List[Dict[str, Any]]:
        """获取当前matplotlib图形信息，但不自动保存"""
        figures_info = []

        # 获取当前所有图形
        fig_nums = plt.get_fignums()

        for fig_num in fig_nums:
            fig = plt.figure(fig_num)
            if fig.get_axes():  # 只处理有内容的图形
                figures_info.append({
                    'figure_number': fig_num,
                    'axes_count': len(fig.get_axes()),
                    'figure_size': fig.get_size_inches().tolist(),
                    'has_content': True
                })

        return figures_info

    def set_sensitive_columns(self, names: Iterable[str]) -> None:
        """Configure columns whose raw values must not appear in table feedback."""
        self.sensitive_columns = {str(name).strip() for name in names if str(name).strip()}
        self._refresh_sensitive_values()

    def _normalized_name(self, value: Any) -> str:
        return str(value).strip()

    def _is_sensitive_name(self, value: Any) -> bool:
        return self._normalized_name(value) in self.sensitive_columns

    def _remember_scalar(self, value: Any) -> None:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return
        text = str(value)
        if text and text.casefold() not in {"nan", "nat", "none"}:
            self._sensitive_values.add(text)

    def _remember_sensitive_values(self, value: Any, seen: set[int] | None = None) -> None:
        """Collect values from configured sensitive table fields for final text masking."""
        if seen is None:
            seen = set()
        value_id = id(value)
        if value_id in seen:
            return
        seen.add(value_id)

        if isinstance(value, pd.DataFrame):
            for position, column in enumerate(value.columns):
                if self._is_sensitive_name(column):
                    for item in value.iloc[:, position].tolist():
                        self._remember_scalar(item)
            self._remember_sensitive_values(value.index, seen)
            return
        if isinstance(value, pd.Series):
            if self._is_sensitive_name(value.index.name):
                for item in value.index.tolist():
                    self._remember_scalar(item)
            if self._is_sensitive_name(value.name) and not self._is_sensitive_name(value.index.name):
                for item in value.tolist():
                    self._remember_scalar(item)
            for label, item in zip(value.index.tolist(), value.tolist()):
                if self._is_sensitive_name(label):
                    self._remember_scalar(item)
            return
        if isinstance(value, pd.Index):
            if self._is_sensitive_name(value.name):
                for item in value.tolist():
                    self._remember_scalar(item)
            return
        if isinstance(value, dict):
            for key, item in value.items():
                self._remember_sensitive_values(key, seen)
                self._remember_sensitive_values(item, seen)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                self._remember_sensitive_values(item, seen)

    def _refresh_sensitive_values(self) -> None:
        for value in self.shell.user_ns.values():
            self._remember_sensitive_values(value)

    def _redact_text(self, text: Any) -> str:
        """Replace known sensitive values at the final model-feedback boundary."""
        redacted = str(text)
        for sensitive_value in sorted(self._sensitive_values, key=len, reverse=True):
            redacted = redacted.replace(sensitive_value, "[REDACTED]")
        return redacted

    def _redact_index(self, index: pd.Index) -> pd.Index:
        if isinstance(index, pd.MultiIndex):
            names = list(index.names)
            masked = [
                tuple(
                    "[REDACTED]" if self._is_sensitive_name(names[position]) else item
                    for position, item in enumerate(values)
                )
                for values in index.tolist()
            ]
            return pd.MultiIndex.from_tuples(masked, names=names)
        if self._is_sensitive_name(index.name):
            return pd.Index(["[REDACTED]"] * len(index), name=index.name)
        return index.copy()

    def _redact_series(self, series: pd.Series) -> pd.Series:
        result = series.copy()
        result.index = self._redact_index(result.index)

        labels = list(result.index)
        for position, label in enumerate(labels):
            if self._is_sensitive_name(label):
                result.iloc[position] = "[REDACTED]"

        if self._is_sensitive_name(series.name):
            if self._is_sensitive_name(series.index.name):
                # value_counts() keeps the sensitive column name on its index;
                # preserve ordinary counts while masking the raw index values.
                result.index = self._redact_index(series.index.set_names(series.name))
            else:
                result = result.astype(object)
                result.iloc[:] = "[REDACTED]"
        return result

    def _redact_table(self, obj: Any) -> Any:
        """Mask display copies while preserving the data used for computation."""
        if isinstance(obj, pd.DataFrame):
            result = obj.copy()
            if any(self._is_sensitive_name(column) for column in result.columns):
                result = result.astype(object)
            for position, column in enumerate(result.columns):
                if self._is_sensitive_name(column):
                    result.iloc[:, position] = "[REDACTED]"
            result.index = self._redact_index(result.index)
            return result
        if isinstance(obj, pd.Series):
            return self._redact_series(obj)
        if isinstance(obj, pd.Index):
            return self._redact_index(obj)
        if isinstance(obj, dict):
            return {
                self._redact_object(key): self._redact_object(value)
                for key, value in obj.items()
            }
        if isinstance(obj, list):
            return [self._redact_object(item) for item in obj]
        if isinstance(obj, tuple):
            return tuple(self._redact_object(item) for item in obj)
        if isinstance(obj, set):
            return {self._redact_object(item) for item in obj}
        if isinstance(obj, frozenset):
            return frozenset(self._redact_object(item) for item in obj)
        return obj

    def _redact_object(self, obj: Any) -> Any:
        return self._redact_table(obj)

    def _safe_object_text(self, obj: Any) -> str:
        return self._redact_text(self._redact_object(obj))

    def _print_redacted(self, *objects: Any, **kwargs: Any) -> None:
        builtins.print(*(self._safe_object_text(obj) for obj in objects), **kwargs)

    def _display_redacted(self, *objects: Any, **kwargs: Any) -> None:
        del kwargs
        for obj in objects:
            builtins.print(self._safe_object_text(obj))

    def _display_table(self, obj: Any, printer: Any, cycle: bool) -> None:
        del cycle
        printer.text(self._safe_object_text(obj))

    def _format_table_output(self, obj: Any) -> str:
        """格式化表格输出，限制行数"""
        obj = self._redact_object(obj)
        if isinstance(obj, pd.DataFrame):
            rows, cols = obj.shape
            print(f"\n数据表形状: {rows}行 x {cols}列")
            print(f"列名: {list(obj.columns)}")

            if rows <= 15:
                return str(obj)
            else:
                head_part = obj.head(5)
                tail_part = obj.tail(5)
                return f"{head_part}\n...\n(省略 {rows-10} 行)\n...\n{tail_part}"

        return self._safe_object_text(obj)

    def execute_code(self, code: str) -> Dict[str, Any]:
        """
        执行代码并返回结果

        Args:
            code: 要执行的Python代码

        Returns:
            {
                'success': bool,
                'output': str,
                'error': str,
                'variables': Dict[str, Any]  # 新生成的重要变量
            }
        """
        # 检查代码安全性
        is_safe, safety_error = self._check_code_safety(code)
        if not is_safe:
            return {
                'success': False,
                'output': '',
                'error': self._redact_text(f"代码安全检查失败: {safety_error}"),
                'variables': {}
            }

        # 记录执行前的变量
        self._refresh_sensitive_values()
        vars_before = set(self.shell.user_ns.keys())

        try:
            # 使用IPython的capture_output来捕获所有输出
            with capture_output() as captured:
                result = self.shell.run_cell(code)

            # 检查执行结果
            if result.error_before_exec:
                error_msg = self._redact_text(result.error_before_exec)
                return {
                    'success': False,
                    'output': self._redact_text(captured.stdout),
                    'error': f"执行前错误: {error_msg}",
                    'variables': {}
                }

            if result.error_in_exec:
                error_msg = self._redact_text(result.error_in_exec)
                return {
                    'success': False,
                    'output': self._redact_text(captured.stdout),
                    'error': f"执行错误: {error_msg}",
                    'variables': {}
                }

            # 获取输出
            output = captured.stdout

            # 如果有返回值，添加到输出
            if result.result is not None:
                formatted_result = self._format_table_output(result.result)
                output += f"\n{formatted_result}"
              # 记录新产生的重要变量（简化版本）
            vars_after = set(self.shell.user_ns.keys())
            new_vars = vars_after - vars_before

            # 只记录新创建的DataFrame等重要数据结构
            important_new_vars = {}
            for var_name in new_vars:
                if not var_name.startswith('_'):
                    try:
                        var_value = self.shell.user_ns[var_name]
                        if hasattr(var_value, 'shape'):  # pandas DataFrame, numpy array
                            important_new_vars[var_name] = f"{type(var_value).__name__} with shape {var_value.shape}"
                        elif var_name in ['session_output_dir']:  # 重要的配置变量
                            important_new_vars[var_name] = str(var_value)
                    except:
                        pass

            self._refresh_sensitive_values()
            return {
                'success': True,
                'output': self._redact_text(output),
                'error': '',
                'variables': important_new_vars
            }
        except Exception as e:
            self._refresh_sensitive_values()
            return {
                'success': False,
                'output': self._redact_text(captured.stdout if 'captured' in locals() else ''),
                'error': self._redact_text(f"执行异常: {str(e)}\n{traceback.format_exc()}"),
                'variables': {}
            }

    def reset_environment(self):
        """重置执行环境"""
        self.shell.reset()
        self._sensitive_values.clear()
        self._setup_common_imports()
        self._setup_chinese_font()
        plt.close('all')
        self.image_counter = 0

    def set_variable(self, name: str, value: Any):
        """设置执行环境中的变量"""
        self.shell.user_ns[name] = value
        self._remember_sensitive_values(value)

    def get_environment_info(self) -> str:
        """获取当前执行环境的变量信息，用于系统提示词"""
        info_parts = []

        # 获取重要的数据变量
        important_vars = {}
        for var_name, var_value in self.shell.user_ns.items():
            if not var_name.startswith('_') and var_name not in ['In', 'Out', 'get_ipython', 'exit', 'quit']:
                try:
                    if hasattr(var_value, 'shape'):  # pandas DataFrame, numpy array
                        important_vars[var_name] = f"{type(var_value).__name__} with shape {var_value.shape}"
                    elif var_name in ['session_output_dir']:  # 重要的路径变量
                        important_vars[var_name] = self._redact_text(var_value)
                    elif isinstance(var_value, (int, float, str, bool)) and len(str(var_value)) < 100:
                        important_vars[var_name] = self._redact_text(
                            f"{type(var_value).__name__}: {var_value}"
                        )
                    elif hasattr(var_value, '__module__') and var_value.__module__ in ['pandas', 'numpy', 'matplotlib.pyplot']:
                        important_vars[var_name] = f"导入的模块: {var_value.__module__}"
                except:
                    continue

        if important_vars:
            info_parts.append("当前环境变量:")
            for var_name, var_info in important_vars.items():
                info_parts.append(self._redact_text(f"- {var_name}: {var_info}"))
        else:
            info_parts.append("当前环境已预装pandas, numpy, matplotlib等库")

        # 添加输出目录信息
        if 'session_output_dir' in self.shell.user_ns:
            info_parts.append(
                self._redact_text(
                    f"图片保存目录: session_output_dir = '{self.shell.user_ns['session_output_dir']}'"
                )
            )

        return "\n".join(info_parts)
