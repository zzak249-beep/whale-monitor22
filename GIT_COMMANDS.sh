#!/bin/bash
# Comandos Git para subir UltraBot a GitHub
# Copia y pega en terminal (sin el # al principio de cada línea)

# ========================================
# 1. CONFIGURAR GIT (primera vez)
# ========================================

git config user.name "Tu Nombre"
git config user.email "tu@email.com"

# ========================================
# 2. INICIALIZAR REPOSITORIO
# ========================================

git init
git branch -M main

# ========================================
# 3. AGREGAR ARCHIVOS
# ========================================

git add .
git status  # Verifica antes de commit

# ========================================
# 4. PRIMER COMMIT
# ========================================

git commit -m "Initial commit: UltraBot v3 complete setup"

# ========================================
# 5. AGREGAR REMOTE (GitHub)
# ========================================

# Reemplaza USUARIO y REPO con tus valores
git remote add origin https://github.com/USUARIO/whale-monitor22.git
# O si usas SSH:
# git remote add origin git@github.com:USUARIO/whale-monitor22.git

# ========================================
# 6. PUSH A GITHUB
# ========================================

git push -u origin main

# ========================================
# VERIFICAR CONEXIÓN
# ========================================

git remote -v  # Muestra origin configurado
git branch -a  # Muestra ramas
git log --oneline  # Muestra commits

# ========================================
# ACTUALIZAR CAMBIOS (luego de esto)
# ========================================

# Después de editar archivos:
git add .
git commit -m "Descripción del cambio"
git push origin main

# ========================================
# ÚTILES
# ========================================

# Ver status
git status

# Ver diferencias
git diff

# Ver historial
git log

# Deshacer último commit (cuidado!)
# git reset --soft HEAD~1

# Clonar repo
# git clone https://github.com/USUARIO/whale-monitor22.git
