"""
Script de prueba completo para pgsqlasync2fast-fastapi

Este script demuestra:
1. Conexión a múltiples bases de datos
2. Ejecución de queries
3. Creación de una base de datos nueva
4. Verificación de permisos de superusuario
"""

import asyncio
from sqlalchemy import text
from pgsqlasync2fast_fastapi import (
    get_manager,
    database_exists,
    create_database,
    drop_database,
    list_databases,
)


async def main():
    """Prueba completa del módulo."""
    
    print("=" * 60)
    print("🧪 PRUEBA COMPLETA DE pgsqlasync2fast-fastapi")
    print("=" * 60)
    
    manager = get_manager()
    
    # 1. Mostrar conexiones configuradas
    print("\n📋 PASO 1: Conexiones configuradas")
    print("-" * 60)
    connections = manager.list_connections()
    print(f"Total de conexiones: {len(connections)}")
    for conn_name in connections:
        is_super = manager.is_superuser_connection(conn_name)
        flag = "🔑 SUPERUSER" if is_super else "👤 Usuario normal"
        print(f"  • {conn_name}: {flag}")
    
    # 2. Probar conexión DEFAULT
    print("\n🔌 PASO 2: Probando conexión DEFAULT")
    print("-" * 60)
    try:
        session = await manager.get_session("default")
        
        # Obtener información de la base de datos
        result = await session.execute(text("SELECT current_database(), current_user"))
        db_name, user = result.fetchone()
        print(f"✅ Conectado a: {db_name}")
        print(f"✅ Usuario: {user}")
        
        # Probar una query simple
        result = await session.execute(text("SELECT 1 + 1 as resultado"))
        resultado = result.scalar()
        print(f"✅ Query de prueba (1+1): {resultado}")
        
        await session.close()
    except Exception as e:
        print(f"❌ Error en conexión DEFAULT: {e}")
    
    # 3. Probar conexión ADMIN (superusuario)
    print("\n🔑 PASO 3: Probando conexión ADMIN (superusuario)")
    print("-" * 60)
    try:
        session = await manager.get_session("admin")
        
        result = await session.execute(text("SELECT current_database(), current_user"))
        db_name, user = result.fetchone()
        print(f"✅ Conectado a: {db_name}")
        print(f"✅ Usuario: {user}")
        
        # Verificar si es superusuario
        result = await session.execute(text("SELECT usesuper FROM pg_user WHERE usename = current_user"))
        is_super = result.scalar()
        print(f"✅ Es superusuario: {'Sí' if is_super else 'No'}")
        
        await session.close()
    except Exception as e:
        print(f"❌ Error en conexión ADMIN: {e}")
    
    # 4. Listar bases de datos existentes
    print("\n📊 PASO 4: Listando bases de datos existentes")
    print("-" * 60)
    try:
        databases = await list_databases()
        print(f"Total de bases de datos: {len(databases)}")
        for db in databases:
            print(f"  • {db}")
    except Exception as e:
        print(f"❌ Error listando bases de datos: {e}")
    
    # 5. Crear una base de datos de prueba
    test_db_name = "prueba_modulo_db"
    print(f"\n🔨 PASO 5: Creando base de datos '{test_db_name}'")
    print("-" * 60)
    try:
        # Verificar si existe
        exists = await database_exists(test_db_name)
        if exists:
            print(f"⚠️  La base de datos '{test_db_name}' ya existe, eliminándola primero...")
            await drop_database(test_db_name, force=True)
        
        # Crear la base de datos
        created = await create_database(test_db_name, owner="test_user")
        if created:
            print(f"✅ Base de datos '{test_db_name}' creada exitosamente")
            
            # Verificar que existe
            exists = await database_exists(test_db_name)
            print(f"✅ Verificación: Base de datos existe = {exists}")
        
    except Exception as e:
        print(f"❌ Error creando base de datos: {e}")
    
    # 6. Conectarse a la nueva base de datos
    print(f"\n🔌 PASO 6: Conectándose a la nueva base de datos")
    print("-" * 60)
    try:
        # Crear una nueva conexión temporal a la base de datos creada
        from pgsqlasync2fast_fastapi.settings import DatabaseConnectionSettings
        from sqlalchemy.ext.asyncio import create_async_engine
        from pydantic import SecretStr
        
        # Usar las credenciales del usuario DEFAULT
        conn_settings = manager.config.get_connection("default")
        temp_url = (
            f"postgresql+asyncpg://{conn_settings.username}:"
            f"{conn_settings.password.get_secret_value()}"
            f"@{conn_settings.host}:{conn_settings.port}/{test_db_name}"
        )
        
        temp_engine = create_async_engine(temp_url, echo=False)
        async with temp_engine.connect() as conn:
            result = await conn.execute(text("SELECT current_database()"))
            db = result.scalar()
            print(f"✅ Conectado exitosamente a: {db}")
            
            # Crear una tabla de prueba
            await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS test_table (
                    id SERIAL PRIMARY KEY,
                    nombre VARCHAR(100),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            await conn.commit()
            print("✅ Tabla 'test_table' creada")
            
            # Insertar datos de prueba
            await conn.execute(text("""
                INSERT INTO test_table (nombre) VALUES ('Prueba 1'), ('Prueba 2')
            """))
            await conn.commit()
            print("✅ Datos de prueba insertados")
            
            # Leer los datos
            result = await conn.execute(text("SELECT * FROM test_table"))
            rows = result.fetchall()
            print(f"✅ Registros encontrados: {len(rows)}")
            for row in rows:
                print(f"   - ID: {row[0]}, Nombre: {row[1]}")
        
        await temp_engine.dispose()
        
    except Exception as e:
        print(f"❌ Error conectándose a la nueva base de datos: {e}")
        import traceback
        traceback.print_exc()
    
    # 7. Limpiar: eliminar la base de datos de prueba
    print(f"\n🗑️  PASO 7: Limpiando - Eliminando '{test_db_name}'")
    print("-" * 60)
    try:
        dropped = await drop_database(test_db_name, force=True)
        if dropped:
            print(f"✅ Base de datos '{test_db_name}' eliminada exitosamente")
    except Exception as e:
        print(f"❌ Error eliminando base de datos: {e}")
    
    # 8. Health checks
    print("\n💚 PASO 8: Health checks de todas las conexiones")
    print("-" * 60)
    for conn_name in connections:
        is_healthy = await manager.health_check(conn_name)
        status = "✅ Saludable" if is_healthy else "❌ No disponible"
        print(f"  • {conn_name}: {status}")
    
    # Cerrar todas las conexiones
    print("\n🔌 Cerrando todas las conexiones...")
    await manager.close_all()
    
    print("\n" + "=" * 60)
    print("✅ PRUEBA COMPLETA FINALIZADA EXITOSAMENTE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
