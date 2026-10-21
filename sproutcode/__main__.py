"""python -m sproutcode 的入口钩子。

Python 约定:-m 执行包时会先运行 __main__.py。真正的 REPL 逻辑在
cli.main(),这里只做转发,保持入口文件极薄——这样 cli.py 可以被测试
直接 import 而不产生副作用。
"""

from .cli import main

if __name__ == "__main__":
    main()
