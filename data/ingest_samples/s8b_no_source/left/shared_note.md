# 同名冲突样例 S8b（左侧来源）

本文件是入库管道样例集 S8 情形二的左份：不带 source 标识提交，目标文件名由文件 stem 派生。另一份同名 stem 的文件位于 right/ 目录，两份在一次提交中先后进入管线。无 source 标识时 slug 退化为 stem，两份不同内容映射到同一规范化目标文件，构成与情形一同构的冲突。

本段是左份文件的内容指纹：LEFT-NOTE-CONTENT-L。无标识路径是历史路径：早期管线没有 source 概念，全部按 stem 命名，语料中三篇 README.md 正是为了解决该路径的互相跳过才引入 source 标识。保留无标识路径的样例覆盖，是因为它仍是代码中的活跃分支——任何调用方只要不传 source_names 就会走到这里。分支无样例等于行为无守卫。

本节其余内容用于把文件撑到可独立成父块的尺寸。左右两份指纹不同、长度接近，store 侧检查方式与 S8a 相同：source 列表只应出现一个来源（当前实现取 stem 作为 source 展示名），规范化目录只应有一个目标文件。任何多出的文件或指纹都是覆盖或重复入库缺陷的证据。This is the content fingerprint of the left note: LEFT-NOTE-CONTENT-L.
