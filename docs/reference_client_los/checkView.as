_global.ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].checkView = function(mapHandler, _loc3_, _loc4_)
{
   var _loc7_ = ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].getCaseCoordonnee(mapHandler,_loc3_);
   var _loc4_ = ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].getCaseCoordonnee(mapHandler,_loc4_);
   var _loc20_ = mapHandler.getCellData(_loc3_);
   var _loc19_ = mapHandler.getCellData(_loc4_);
   var _loc18_ = !!_loc20_.spriteOnID ? 1.5 : 0;
   var _loc17_ = !!_loc19_.spriteOnID ? 1.5 : 0;
   _loc18_ += !!_loc20_.carriedSpriteOnId ? 1.5 : 0;
   _loc17_ += !!_loc19_.carriedSpriteOnId ? 1.5 : 0;
   _loc7_.z = mapHandler.getCellHeight(_loc3_) + _loc18_;
   _loc4_.z = mapHandler.getCellHeight(_loc4_) + _loc17_;
   var _loc11_ = _loc4_.z - _loc7_.z;
   var _loc12_ = Math.max(Math.abs(_loc7_.y - _loc4_.y),Math.abs(_loc7_.x - _loc4_.x));
   var _loc14_ = (_loc7_.y - _loc4_.y) / (_loc7_.x - _loc4_.x);
   var _loc16_ = _loc7_.y - _loc14_ * _loc7_.x;
   var _loc6_ = _loc4_.x - _loc7_.x < 0 ? -1 : 1;
   var _loc2_ = _loc4_.y - _loc7_.y < 0 ? -1 : 1;
   var _loc13_ = _loc7_.y;
   var _loc24_ = _loc7_.x;
   var _loc15_ = _loc4_.x * _loc6_;
   var _loc23_ = _loc4_.y * _loc2_;
   var _loc3_ = _loc7_.x + 0.5 * _loc6_;
   while(_loc3_ * _loc6_ <= _loc15_)
   {
      var _loc5_ = _loc14_ * _loc3_ + _loc16_;
      if(_loc2_ > 0)
      {
         var _loc10_ = Math.round(_loc5_);
         var _loc8_ = Math.ceil(_loc5_ - 0.5);
      }
      else
      {
         _loc10_ = Math.ceil(_loc5_ - 0.5);
         _loc8_ = Math.round(_loc5_);
      }
      var _loc1_ = _loc13_;
      while(_loc1_ * _loc2_ <= _loc8_ * _loc2_)
      {
         if(!ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].checkCellView(mapHandler,_loc3_ - _loc6_ / 2,_loc1_,false,_loc7_,_loc4_,_loc11_,_loc12_))
         {
            return false;
         }
         _loc1_ += _loc2_;
      }
      _loc13_ = _loc10_;
      _loc3_ += _loc6_;
   }
   _loc1_ = _loc13_;
   while(true)
   {
      if(_loc1_ * _loc2_ > _loc4_.y * _loc2_)
      {
         break;
      }
      if(!ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].checkCellView(mapHandler,_loc3_ - 0.5 * _loc6_,_loc1_,false,_loc7_,_loc4_,_loc11_,_loc12_))
      {
         return false;
      }
      _loc1_ += _loc2_;
   }
   if(!ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].checkCellView(mapHandler,_loc3_ - 0.5 * _loc6_,_loc1_ - _loc2_,true,_loc7_,_loc4_,_loc11_,_loc12_))
   {
      return false;
   }
   return true;
};
_global.ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].checkCellView = function(mapHandler, _loc3_, _loc4_, _loc5_, _loc6_, _loc7_, _loc8_, _loc9_)
{
   var _loc3_ = ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].getCaseNum(mapHandler,_loc3_,_loc4_);
   var _loc2_ = mapHandler.getCellData(_loc3_);
   var _loc4_ = Math.max(Math.abs(_loc6_.y - _loc4_),Math.abs(_loc6_.x - _loc3_));
   var _loc5_ = _loc4_ / _loc9_ * _loc8_ + _loc6_.z;
   var _loc11_ = mapHandler.getCellHeight(_loc3_);
   var _loc12_ = _loc2_.spriteOnID == undefined || _loc2_.spriteOnID != undefined && _global.API.datacenter.Sprites.getItemAt(_loc2_.spriteOnID).CharacteristicsManager.hasEffect(150);
   var _loc10_ = _loc12_ || (_loc4_ == 0 || (_loc5_ || _loc7_.x == _loc3_ && _loc7_.y == _loc4_)) ? false : true;
   if(_loc2_.lineOfSight && (_loc2_.active && (_loc11_ <= _loc5_ && !_loc10_)))
   {
      return true;
   }
   if(_loc5_)
   {
      return true;
   }
   return false;
};
ank.battlefield["\x1e\n\t"]["\x1e\x16\x1b"].cellIsAroundFrom = function(mapHandler, nCellNumFrom, nCellNum)
{
   var _loc2_ = mapHandler.getWidth();
   var _loc1_ = [1,_loc2_,_loc2_ * 2 - 1,_loc2_ - 1,-1,- _loc2_,(- _loc2_) * 2 + 1,- _loc2_ - 1];
   for(var _loc5_ in _loc1_)
   {
      if(nCellNumFrom + _loc1_[_loc5_] == nCellNum)
      {
         return true;
      }
   }
   return false;
};
