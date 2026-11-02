# ADR 0002：应用数据库与 checkpoint 双状态所有权

应用数据库拥有 Run/Task/事件/产物/租约/预算/取消；LangGraph checkpoint 拥有图节点恢复所需的序列化状态。两者不能假设同一事务，Worker 完成节点后显式更新应用数据库并在租约有效时提交；恢复时以应用状态做访问控制、以 checkpoint 续接图，再进行对账。

