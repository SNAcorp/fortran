``` mermaid
graph TD
    A[Загрузка zip архива на сервер] --> B[Добавление фоновой задачи для обработки]
    B --> C{Фоновая задача}
    C --> D[Распаковка архива]
    D --> E[Проверка наличия всех типов Фортран файлов]
    E --> F{Все типы Фортран файлов найдены?}
    F -->|Нет| G[Выбросить exception]
    F -->|Да| H[Проверка проекта на работоспособность]
    H --> I[Отправка статуса Check пользователю]
    I --> J[Компиляция проекта]
    J --> K{Компиляция успешна?}
    K -->|Нет| G[Выбросить exception]
    K -->|Да| L[Отправка статуса Processing пользователю]
    L --> M[Добавление директивы IMPLICIT none в файл]
    M --> N[Компиляция проекта]
    N --> O[Анализ ошибок компиляции]
    O --> P[Вычленение неявных переменных]
    P --> Q[Создание директивы !$omp threadprivate для каждой неявной переменной]
    Q --> R[Удаление IMPLICIT none]
    R --> S[Сборка проекта]
    S --> T{Сборка успешна?}
    T -->|Нет| G[Выбросить exception]
    T -->|Да| U[Отправка статуса Ready пользователю]
```
